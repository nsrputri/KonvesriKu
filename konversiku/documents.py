"""Dokumen tahap 1: TXT, Markdown, RTF, HTML, reStructuredText, LaTeX, DOCX, PDF.

Pipeline (tidak ada yang hanya mengganti ekstensi):
* TXT dan teks hasil ekstraksi PDF diperlakukan sebagai teks biasa: di-escape ke HTML
  lalu dibaca Pandoc, sehingga karakter khusus Markdown/RST/LaTeX tidak menjadi markup.
* MD (gfm), RTF, HTML, RST, LaTeX, DOCX dibaca reader Pandoc dengan ``--sandbox``;
  AST disanitasi di Python (gambar, blok mentah, atribut, tautan berbahaya dihapus)
  sebelum ditulis writer Pandoc.
* Target PDF: dokumen ditulis ke DOCX dulu (Pandoc atau python-docx), lalu
  LibreOffice headless mengubahnya ke PDF. Tidak ada LaTeX engine yang dijalankan.
* PDF input: pypdfium2 (PDFium) membaca text layer saja di proses terpisah (tanpa OCR,
  tata letak tidak dijaga).
"""
from __future__ import annotations

import atexit
import html
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from . import limits, ooxml, tools
from .common import (ConversionError, ConversionTimeout, Result, check_output, check_text_length,
                     decode_utf8, format_count, request_tempdir)

logging.getLogger("pypdf").setLevel(logging.ERROR)

SAFE_SCHEMES = {"", "http", "https", "mailto"}
CONTROL_CHARS = re.compile(r"[\x01-\x08\x0e-\x1f\x7f]")
HTML_TAG = re.compile(r"<(?:!doctype|!--|[a-zA-Z][a-zA-Z0-9-]*)[\s>/]", re.IGNORECASE)
WRITER_EXTRA = {"plain": ["--wrap=none"], "gfm": ["--wrap=none"], "html5": ["--wrap=none"],
                "rst": ["--wrap=none"], "latex": ["--wrap=none"], "rtf": [], "docx": []}

HTML_PAGE = """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Hasil KonversiKu</title>
<style>body{{max-width:48em;margin:2em auto;padding:0 1em;font:16px/1.6 system-ui,sans-serif}}table{{border-collapse:collapse}}td,th{{border:1px solid #bbb;padding:.25em .5em}}</style>
</head>
<body>
{body}
</body>
</html>
"""


# ------------------------------------------------------- validasi isi teks

def read_text(raw: bytes, source: str) -> str:
    """Validasi isi (bukan hanya ekstensi) sejauh format teks memungkinkan."""
    text = decode_utf8(raw)
    if CONTROL_CHARS.search(text):
        raise ConversionError("File ini bukan teks biasa (berisi karakter biner atau kontrol).")
    if source == "rtf" and not text.lstrip("﻿ \t\r\n").startswith("{\\rtf"):
        raise ConversionError("Isi file bukan RTF (dokumen RTF selalu diawali {\\rtf).")
    if source == "html" and not HTML_TAG.search(text):
        raise ConversionError("Isi file tidak terlihat seperti HTML (tidak ada tag HTML).")
    return text


# ------------------------------------------------------------- sanitasi AST

def _is_attr(value) -> bool:
    return (isinstance(value, list) and len(value) == 3 and isinstance(value[0], str)
            and isinstance(value[1], list) and all(isinstance(c, str) for c in value[1])
            and isinstance(value[2], list)
            and all(isinstance(kv, list) and len(kv) == 2 and all(isinstance(x, str) for x in kv)
                    for kv in value[2]))


def _safe_url(url: str) -> bool:
    try:
        return urlsplit(url.strip()).scheme.lower() in SAFE_SCHEMES
    except ValueError:
        return False


def _sanitize(node, stats: Counter):
    if isinstance(node, list):
        if _is_attr(node):
            stats["attributes"] += len(node[2])
            return [node[0], node[1], []]
        out = []
        for item in node:
            if isinstance(item, dict) and "t" in item:
                kind = item["t"]
                if kind in ("RawBlock", "RawInline"):
                    stats["raw"] += 1
                    continue
                if kind == "Image":
                    stats["images"] += 1
                    out.extend(_sanitize(item["c"][1], stats))
                    continue
                if kind == "Link" and not _safe_url(item["c"][2][0]):
                    stats["links"] += 1
                    out.extend(_sanitize(item["c"][1], stats))
                    continue
            out.append(_sanitize(item, stats))
        return out
    if isinstance(node, dict):
        return {key: _sanitize(value, stats) for key, value in node.items()}
    return node


def sanitize_ast(ast: dict) -> Counter:
    stats: Counter = Counter()
    ast["blocks"] = _sanitize(ast.get("blocks", []), stats)
    ast["meta"] = _sanitize(ast.get("meta", {}), stats)
    return stats


def _sanitize_notes(stats: Counter) -> list[str]:
    notes = []
    if stats["images"]:
        notes.append(f"{stats['images']} gambar dihapus (diganti teks alt); tidak ada yang diambil dari luar.")
    if stats["raw"]:
        notes.append(f"{stats['raw']} blok mentah (HTML/LaTeX/skrip) dihapus.")
    if stats["links"]:
        notes.append(f"{stats['links']} tautan dengan skema tidak aman diubah menjadi teks.")
    return notes


# ------------------------------------------------------------------ pandoc

def _require(requirement: str) -> tools.Tools:
    found = tools.detect()
    reason = tools.available(found, requirement)
    if reason:
        raise ConversionError(reason + ".", 503)
    return found


def _pandoc_base(found: tools.Tools) -> list[str]:
    return [found.pandoc, *found.pandoc_rts, "--sandbox"]


def pandoc_read(data: bytes, source: str) -> tuple[dict, Counter]:
    reader = tools.READERS[source]
    found = _require(f"pandoc-in:{reader}")
    with request_tempdir() as workdir:
        path = workdir / f"input.{source}"      # nama tetap; nama file pengguna tidak dipakai
        path.write_bytes(data)
        try:
            raw_ast = tools.run([*_pandoc_base(found), "-f", reader, "-t", "json", str(path)],
                                timeout=limits.PANDOC_TIMEOUT, cwd=str(workdir))
            ast = json.loads(raw_ast)
        except tools.ToolFailed as exc:
            raise ConversionError(f"{source.upper()} tidak bisa dibaca. Periksa apakah file valid.") from exc
    stats = sanitize_ast(ast)
    return ast, stats


def pandoc_write(ast: dict, target: str) -> bytes:
    writer = tools.WRITERS[target]
    found = _require(f"pandoc-out:{writer}")
    args = [*_pandoc_base(found), "-f", "json", "-t", writer, *WRITER_EXTRA[writer],
            *found.pandoc_writer_args.get(writer, ()), "-o", "-"]
    with request_tempdir() as workdir:
        output = tools.run(args, timeout=limits.PANDOC_TIMEOUT, cwd=str(workdir),
                           input_bytes=json.dumps(ast).encode("utf-8"))
    check_output(output)
    if target == "html":
        output = HTML_PAGE.format(body=output.decode("utf-8").strip()).encode("utf-8")
    return output


# ----------------------------------------------------------- teks biasa

def text_to_html_body(text: str) -> str:
    parts = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n\n"):
        if paragraph.strip():
            lines = [html.escape(line, quote=False) for line in paragraph.split("\n")]
            parts.append("<p>" + "<br>\n".join(lines) + "</p>")
    return "\n".join(parts)


def text_to_docx(text: str) -> bytes:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.text.paragraph import Paragraph

    try:
        document = Document()
        # Document.add_paragraph mencari sectPr di antara semua paragraf setiap kali (kuadratik);
        # paragraf disisipkan langsung sebelum sectPr agar TXT berbaris banyak tetap linear.
        body = document.element.body
        end = body.sectPr
        for line in text.split("\n"):
            p = OxmlElement("w:p")
            if end is not None:
                end.addprevious(p)
            else:
                body.append(p)
            line = line.rstrip("\r")
            if line:
                Paragraph(p, document._body).add_run(line)
        output = io.BytesIO()
        document.save(output)
    except (ValueError, TypeError) as exc:
        raise ConversionError("Teks berisi karakter kontrol yang tidak didukung DOCX.") from exc
    return check_output(output.getvalue())


def _from_text(text: str, target: str) -> Result:
    if target == "txt":
        return Result(check_output(text.encode("utf-8")))
    if target == "docx":
        return Result(text_to_docx(text))
    if target == "html":
        return Result(check_output(HTML_PAGE.format(body=text_to_html_body(text)).encode("utf-8")))
    if target == "pdf":
        return Result(libreoffice_pdf(text_to_docx(text)))
    page = f"<html><body>{text_to_html_body(text)}</body></html>".encode("utf-8")
    ast, stats = pandoc_read(page, "html")
    return Result(pandoc_write(ast, target), _sanitize_notes(stats))


def _via_pandoc(data: bytes, source: str, target: str) -> Result:
    ast, stats = pandoc_read(data, source)
    if target == "pdf":
        return Result(libreoffice_pdf(pandoc_write(ast, "docx")), _sanitize_notes(stats))
    return Result(pandoc_write(ast, target), _sanitize_notes(stats))


# ------------------------------------------------------------------ DOCX

def docx_to_text(raw: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(io.BytesIO(bytes(raw)))
        lines = []
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                lines.append(block.text)
            elif isinstance(block, Table):
                lines.extend("\t".join(cell.text for cell in row.cells) for row in block.rows)
    except Exception as exc:  # python-docx melempar beragam error untuk file rusak
        raise ConversionError("DOCX tidak valid atau rusak. Gunakan file .docx asli (bukan .doc).") from exc
    text = "\n".join(lines)
    if not text.strip():
        raise ConversionError("DOCX tidak berisi teks yang bisa dibaca.")
    return check_text_length(text)


# ------------------------------------------------------------- LibreOffice

_lo_lock = threading.Lock()


def _lo_profile() -> Path:
    root = os.environ.get("KONVERSIKU_LO_PROFILE_ROOT") or tempfile.gettempdir()
    return Path(root) / f"konversiku-lo-profile-{os.getpid()}"


@atexit.register
def _remove_lo_profile() -> None:
    shutil.rmtree(_lo_profile(), ignore_errors=True)


def libreoffice_pdf(docx: bytes) -> bytes:
    """DOCX (sudah diperiksa/dibuat aplikasi) ke PDF dengan LibreOffice headless."""
    found = _require("libreoffice")
    with request_tempdir() as workdir:
        source_path = workdir / "input.docx"
        outdir = workdir / "out"
        outdir.mkdir()
        source_path.write_bytes(docx)
        profile = _lo_profile()
        args = [found.libreoffice, f"-env:UserInstallation={profile.as_uri()}", "--headless",
                "--norestore", "--nolockcheck", "--nologo", "--convert-to", "pdf:writer_pdf_Export",
                "--outdir", str(outdir), str(source_path)]
        with _lo_lock:   # satu profil per proses: konversi LibreOffice bergantian
            try:
                tools.run(args, timeout=limits.LIBREOFFICE_TIMEOUT, cwd=str(workdir))
            except ConversionTimeout:
                shutil.rmtree(profile, ignore_errors=True)   # profil bisa rusak setelah kill
                raise
            except tools.ToolFailed as exc:
                raise ConversionError("LibreOffice gagal membuat PDF dari dokumen ini.") from exc
        result = outdir / "input.pdf"
        if not result.is_file():
            raise ConversionError("LibreOffice tidak menghasilkan PDF untuk dokumen ini.")
        if result.stat().st_size > limits.MAX_OUTPUT_BYTES:
            raise ConversionError(
                f"Hasil konversi terlalu besar (maksimal {limits.size_label(limits.MAX_OUTPUT_BYTES)}).")
        data = result.read_bytes()
    if not data.startswith(b"%PDF"):
        raise ConversionError("LibreOffice tidak menghasilkan PDF yang valid.")
    return data


# ------------------------------------------------------------------ PDF

PDF_SCRIPT = Path(__file__).with_name("pdf_extract.py")


def pdf_text(raw: bytes) -> str:
    """Text layer PDF via pypdfium2 di proses terpisah (isolasi, timeout, batas memori)."""
    if b"%PDF-" not in bytes(raw[:1024]):
        raise ConversionError("File ini bukan PDF (tanda %PDF- tidak ditemukan di awal file).")
    with request_tempdir() as workdir:
        path = workdir / "input.pdf"
        path.write_bytes(raw)
        args = [sys.executable, "-I", str(PDF_SCRIPT), str(path), str(limits.MAX_PDF_PAGES),
                str(limits.MAX_TEXT_CHARS), str(limits.PDF_MEMORY_BYTES)]
        try:
            reply = json.loads(tools.run(args, timeout=limits.PDF_TIMEOUT, cwd=str(workdir)))
        except tools.ToolFailed as exc:
            raise ConversionError("PDF tidak valid atau rusak (pembaca PDF berhenti).") from exc
        except ValueError as exc:
            raise ConversionError("PDF tidak valid atau rusak.") from exc
    problem = reply.get("error")
    if problem == "encrypted":
        raise ConversionError(
            "PDF terenkripsi atau bersandi. Buka kuncinya dulu; KonversiKu tidak membuka PDF bersandi.")
    if problem == "pages":
        raise ConversionError(f"PDF memiliki {reply['pages']} halaman; maksimal {limits.MAX_PDF_PAGES} halaman.")
    if problem == "chars":
        raise ConversionError(f"Teks PDF terlalu panjang (maksimal {format_count(limits.MAX_TEXT_CHARS)} karakter).")
    if problem:
        raise ConversionError("PDF tidak valid atau rusak.")
    pages = [page.strip("\n") for page in reply.get("pages", [])]
    text = "\n\n".join(pages).strip()
    if not text:
        raise ConversionError("PDF tidak memiliki text layer (mungkin hasil pindai). OCR tidak didukung.")
    return text + "\n"


# ---------------------------------------------------------------- pasangan

def converter(source: str, target: str):
    def convert(raw: bytes) -> Result:
        if source == "pdf":
            return _from_text(pdf_text(raw), target)
        if source == "txt":
            return _from_text(read_text(raw, "txt"), target)
        if source == "docx":
            archive = ooxml.check_docx(raw)
            if target == "txt":
                return Result(check_output(docx_to_text(raw).encode("utf-8")))
            clean = ooxml.repack(archive)
            if target == "pdf":
                return Result(libreoffice_pdf(clean))
            return _via_pandoc(clean, "docx", target)
        read_text(raw, source)
        return _via_pandoc(bytes(raw), source, target)
    return convert


def requirements(source: str, target: str) -> tuple[str, ...]:
    """Alat sistem yang dibutuhkan pasangan ini, sesuai pipeline di atas."""
    needs: list[str] = []
    plain_source = source in ("txt", "pdf")
    if source == "docx" and target == "txt":
        return ()
    if plain_source and target in ("txt", "docx", "html"):
        return ()
    if source == "docx" and target == "pdf":
        return ("libreoffice",)
    if plain_source and target == "pdf":
        return ("libreoffice",)
    needs.append(f"pandoc-in:{tools.READERS['html' if plain_source else source]}")
    writer_target = "docx" if target == "pdf" else target
    needs.append(f"pandoc-out:{tools.WRITERS[writer_target]}")
    if target == "pdf":
        needs.append("libreoffice")
    return tuple(needs)
