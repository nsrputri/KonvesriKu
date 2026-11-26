"""Matriks format tahap 1 (PRD Bagian 6). Satu-satunya sumber kebenaran untuk API dan UI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import data, documents, limits, tools

DOCS = ["txt", "md", "rtf", "html", "rst", "tex"]          # kelompok dokumen teks D
DATA = ["csv", "tsv", "json", "yaml", "xml"]               # kelompok data S

LABELS = {
    "txt": "Teks biasa", "md": "Markdown", "rtf": "Rich Text Format", "html": "HTML",
    "rst": "reStructuredText", "tex": "LaTeX", "docx": "Word", "pdf": "PDF",
    "csv": "CSV", "tsv": "TSV", "json": "JSON", "yaml": "YAML", "xml": "XML",
}
ALIASES = {"markdown": "md", "htm": "html", "yml": "yaml"}
MEDIA_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "rtf": "application/rtf",
    "html": "text/html; charset=utf-8",
    "rst": "text/x-rst; charset=utf-8",
    "tex": "application/x-tex",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "csv": "text/csv; charset=utf-8",
    "tsv": "text/tab-separated-values; charset=utf-8",
    "json": "application/json",
    "yaml": "application/yaml",
    "xml": "application/xml",
}
TOOL_LABELS = {"pandoc": "Pandoc", "libreoffice": "LibreOffice"}


@dataclass(frozen=True)
class Pair:
    source: str
    target: str
    run: Callable
    requires: tuple[str, ...]
    notes: tuple[str, ...]
    options: tuple[str, ...] = ()


# ------------------------------------------------------------ catatan pasangan

def _doc_notes(source: str, target: str) -> tuple[str, ...]:
    notes = []
    if source == "pdf":
        notes.append("Hanya text layer, tanpa OCR. Tata letak, tabel, dan gambar PDF tidak dijaga.")
    elif source == "docx":
        notes.append("Gambar, header/footer, dan tata letak DOCX tidak ikut.")
    elif source == "txt":
        notes.append("Teks dianggap teks biasa; karakter khusus di-escape sehingga tidak menjadi format.")
    else:
        notes.append("Gambar, blok HTML/LaTeX mentah, dan skrip dihapus; tidak ada resource yang diambil "
                     "dari URL atau file lokal.")
    if source == "tex":
        notes.append("LaTeX tidak dikompilasi: \\input, \\include, dan paket tidak dijalankan; hanya struktur "
                     "yang dikenali Pandoc.")
    if source == "rtf":
        notes.append("Gaya RTF yang kompleks bisa hilang.")
    if target == "txt":
        notes.append("Format (tebal, miring, tabel) hilang; hanya teks.")
    elif target == "pdf":
        notes.append("PDF dibuat lewat DOCX lalu LibreOffice; tata letak sederhana dan font mengikuti server.")
    elif target == "tex":
        notes.append("LaTeX hasil tidak dikompilasi oleh server.")
    elif target == "html":
        notes.append("HTML mentah, skrip, dan atribut dihapus; hasil selalu diunduh, tidak dipratinjau.")
    elif target in ("md", "rst", "rtf", "docx"):
        notes.append("Struktur dasar (judul, paragraf, daftar, tabel) dipertahankan sebisanya.")
    return tuple(notes)


FLATTEN = ("Data bersarang diratakan: objek memakai titik (a.b), list memakai indeks (a[0]); null dan "
           "kunci yang tidak ada menjadi sel kosong; objek/list kosong ditulis {} atau [].")
FORMULA = ("Sel berawalan =, +, -, @ bisa dijalankan spreadsheet sebagai formula. Pengaman formula "
           "(bawaan aktif) menambahkan ' di depannya.")


def _data_notes(source: str, target: str) -> tuple[str, ...]:
    notes = []
    if source in ("csv", "tsv"):
        notes.append("Nilai tetap teks (001 tetap 001); header bertitik tidak dibuka menjadi objek.")
    if source == "yaml":
        notes.append("Skema YAML 1.2 core: yes/no dan tanggal tetap teks. Komentar hilang; alias dibatasi.")
    if source == "xml":
        notes.append("Konvensi XML: atribut menjadi @nama, teks beratribut menjadi #text, elemen berulang "
                     "menjadi list, elemen kosong menjadi null, nilai menjadi teks. Komentar dan DTD tidak dibawa.")
    if target in ("csv", "tsv"):
        if source not in ("csv", "tsv"):
            notes.append(FLATTEN)
        notes.append(FORMULA)
    if target == "xml":
        notes.append("Tipe data menjadi teks; null, objek kosong, dan list kosong menjadi elemen kosong; "
                     "list berisi satu elemen terbaca kembali sebagai objek; round-trip tidak dijamin.")
    if target == "yaml":
        notes.append("YAML ditulis tanpa alias; komentar tidak ada.")
    return tuple(notes)


def _build() -> list[Pair]:
    pairs = []
    for source in DOCS:
        for target in [t for t in DOCS if t != source] + ["docx", "pdf"]:
            pairs.append(Pair(source, target, documents.converter(source, target),
                              documents.requirements(source, target), _doc_notes(source, target)))
    for target in DOCS + ["pdf"]:
        pairs.append(Pair("docx", target, documents.converter("docx", target),
                          documents.requirements("docx", target), _doc_notes("docx", target)))
    for target in ("txt", "md", "docx"):
        pairs.append(Pair("pdf", target, documents.converter("pdf", target),
                          documents.requirements("pdf", target), _doc_notes("pdf", target)))
    for source in DATA:
        for target in [t for t in DATA if t != source]:
            options = ("formula_guard",) if target in ("csv", "tsv") else ()
            pairs.append(Pair(source, target, data.converter(source, target), (),
                              _data_notes(source, target), options))
    return pairs


PAIRS = _build()
BY_KEY = {(p.source, p.target): p for p in PAIRS}
SOURCE_ORDER = DOCS + ["docx", "pdf"] + DATA


def canonical(name: str | None) -> str:
    value = (name or "").strip().lower().lstrip(".")
    return ALIASES.get(value, value)


def get(source: str, target: str) -> Pair | None:
    return BY_KEY.get((canonical(source), canonical(target)))


def unavailable_reason(pair: Pair, found: tools.Tools) -> str | None:
    reasons = []
    for requirement in pair.requires:
        reason = tools.available(found, requirement)
        if reason and reason not in reasons:
            reasons.append(reason)
    if not reasons:
        return None
    return f"{'; '.join(reasons)}, jadi {pair.source.upper()} ke {pair.target.upper()} dinonaktifkan."


def describe() -> dict:
    found = tools.detect()
    pairs: dict[str, list[str]] = {}
    disabled: dict[str, dict[str, str]] = {}
    notes: dict[str, dict[str, list[str]]] = {}
    options: dict[str, dict[str, list[str]]] = {}
    for source in SOURCE_ORDER:
        pairs[source] = []
        for pair in (p for p in PAIRS if p.source == source):
            reason = unavailable_reason(pair, found)
            if reason:
                disabled.setdefault(source, {})[pair.target] = reason
            else:
                pairs[source].append(pair.target)
            notes.setdefault(source, {})[pair.target] = list(pair.notes)
            if pair.options:
                options.setdefault(source, {})[pair.target] = list(pair.options)
    return {
        "pairs": pairs,
        "disabled": disabled,
        "aliases": dict(ALIASES),
        "labels": dict(LABELS),
        "groups": {"Dokumen": DOCS + ["docx", "pdf"], "Data": DATA},
        "notes": notes,
        "options": options,
        "limits": limits.public(),
        "tools": {
            "pandoc": found.pandoc_version if found.pandoc else None,
            "pandoc_readers": sorted(found.pandoc_readers),
            "pandoc_writers": sorted(found.pandoc_writers),
            "libreoffice": found.libreoffice_version if found.libreoffice else None,
        },
    }
