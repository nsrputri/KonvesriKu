"""Pemeriksaan ZIP DOCX sebelum dimuat oleh library atau alat eksternal.

Tidak ada entri yang diekstrak ke filesystem. Ukuran dibaca dari central
directory lalu diverifikasi dengan membaca ulang setiap entri lewat ``zipfile``
(yang berhenti pada ukuran yang dideklarasikan dan memeriksa CRC).
"""
from __future__ import annotations

import io
import re
import zipfile

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from . import limits
from .common import ConversionError, format_count

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
DANGEROUS_FIELDS = re.compile(
    r"\b(INCLUDETEXT|INCLUDEPICTURE|INCLUDE|LINK|DDEAUTO|DDE|IMPORT)\b", re.IGNORECASE)
OFFICE_DOCUMENT = "/officeDocument"
MAIN_PART = "word/document.xml"

HINTS = {"DOCX": "Gunakan file .docx asli (bukan .doc)."}
REQUIRED = {"DOCX": ("[Content_Types].xml", "word/document.xml")}


def _invalid(kind: str) -> ConversionError:
    return ConversionError(f"{kind} tidak valid atau rusak. {HINTS[kind]}")


def _safe_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    return ".." not in name.split("/")


def open_checked(raw: bytes, kind: str) -> zipfile.ZipFile:
    """Kembalikan ZipFile yang sudah lolos batas ukuran, nama, dan integritas."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(raw)))
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise _invalid(kind) from exc
    infos = archive.infolist()
    if len(infos) > limits.MAX_ZIP_ENTRIES:
        raise ConversionError(
            f"Isi {kind} terlalu banyak (maksimal {format_count(limits.MAX_ZIP_ENTRIES)} entri ZIP).")
    total = 0
    names = set()
    for info in infos:
        total += info.file_size
        if total > limits.MAX_ZIP_EXPANDED_BYTES:
            raise ConversionError(
                f"Isi {kind} terlalu besar (maksimal {limits.size_label(limits.MAX_ZIP_EXPANDED_BYTES)} setelah dibuka).")
        if not _safe_name(info.filename) or info.filename in names:
            raise ConversionError(f"{kind} berisi nama entri yang tidak aman atau ganda.")
        if info.flag_bits & 0x1:
            raise ConversionError(f"{kind} terenkripsi; simpan ulang tanpa sandi lalu coba lagi.")
        names.add(info.filename)
    for required in REQUIRED[kind]:
        if required not in names:
            raise _invalid(kind)
    try:
        # Membaca setiap entri: ukuran nyata tidak bisa melebihi yang dideklarasikan,
        # dan CRC yang tidak cocok ditolak.
        if archive.testzip() is not None:
            raise _invalid(kind)
    except (zipfile.BadZipFile, ValueError, OSError, EOFError, NotImplementedError) as exc:
        raise _invalid(kind) from exc
    return archive


def _parse_xml(archive: zipfile.ZipFile, name: str, kind: str):
    try:
        return SafeET.fromstring(archive.read(name))
    except (SafeET.ParseError, DefusedXmlException, ValueError) as exc:
        raise _invalid(kind) from exc


def _is_xml(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bool:
    """Dikenali dari isinya, bukan nama: pembaca DOCX mencari bagian lewat relasi, bukan ekstensi."""
    with archive.open(info) as entry:
        head = entry.read(64)
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    return head.removeprefix(b"\xef\xbb\xbf").lstrip(b" \t\r\n").startswith(b"<")


def check_docx(raw: bytes) -> zipfile.ZipFile:
    """Tolak DOCX dengan sumber eksternal, field yang dapat mengambil file/URL, bagian utama
    yang tidak standar, atau XML yang terlalu besar untuk Pandoc/LibreOffice."""
    archive = open_checked(raw, "DOCX")
    xml_parts = [info for info in archive.infolist() if _is_xml(archive, info)]
    if sum(info.file_size for info in xml_parts) > limits.MAX_DOCX_XML_BYTES:
        raise ConversionError(
            f"Isi teks DOCX terlalu besar untuk diproses (maksimal {limits.size_label(limits.MAX_DOCX_XML_BYTES)} "
            "XML setelah dibuka). Pecah dokumen menjadi beberapa bagian lalu coba lagi.")
    for info in xml_parts:
        name = info.filename
        if name.endswith(".rels"):
            root = _parse_xml(archive, name, "DOCX")
            for rel in root.iter(f"{REL_NS}Relationship"):
                if rel.get("TargetMode") == "External" and not rel.get("Type", "").endswith("/hyperlink"):
                    raise ConversionError(
                        "DOCX memuat sumber eksternal (gambar, template, atau objek tertaut). "
                        "Sematkan isinya atau hapus tautannya, lalu coba lagi.")
                if name == "_rels/.rels" and rel.get("Type", "").endswith(OFFICE_DOCUMENT) \
                        and rel.get("Target", "").lstrip("/") != MAIN_PART:
                    raise ConversionError(
                        f"DOCX tidak standar: isi utama harus berada di {MAIN_PART}. Simpan ulang dengan "
                        "Word atau LibreOffice lalu coba lagi.")
        elif name.lower().endswith(".xml"):
            _check_fields(_parse_xml(archive, name, "DOCX"))
    return archive


def _check_fields(root) -> None:
    """Kumpulkan instruksi field (bisa terpecah di banyak run dan bersarang)."""
    instructions: list[str] = []
    stack: list[list[str] | None] = []
    for element in root.iter():
        if element.tag == f"{W_NS}fldSimple":
            instructions.append(element.get(f"{W_NS}instr", ""))
        elif element.tag == f"{W_NS}fldChar":
            kind = element.get(f"{W_NS}fldCharType")
            if kind == "begin":
                stack.append([])
            elif kind == "separate" and stack and stack[-1] is not None:
                instructions.append("".join(stack[-1]))
                stack[-1] = None
            elif kind == "end" and stack:
                parts = stack.pop()
                if parts is not None:
                    instructions.append("".join(parts))
        elif element.tag == f"{W_NS}instrText" and stack and stack[-1] is not None:
            stack[-1].append(element.text or "")
    instructions.extend("".join(parts) for parts in stack if parts)
    for instruction in instructions:
        match = DANGEROUS_FIELDS.search(instruction)
        if match:
            name = match.group(1).upper()
            raise ConversionError(
                f"DOCX memuat field {name} yang dapat mengambil file atau URL luar. "
                "Hapus field itu lalu coba lagi.")


def repack(archive: zipfile.ZipFile) -> bytes:
    """Tulis ulang ZIP yang sudah diverifikasi untuk diberikan ke alat eksternal.

    Alat seperti LibreOffice memakai pembaca ZIP sendiri; menulis ulang memastikan
    yang mereka baca sama persis dengan yang sudah diperiksa di sini.
    """
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as clean:
        for info in archive.infolist():
            clean.writestr(info.filename, archive.read(info))
    return out.getvalue()
