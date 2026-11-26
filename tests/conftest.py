"""Fixture, contoh input, dan parser output untuk pengujian KonversiKu tahap 1.

Semua contoh dibuat di memori oleh library yang relevan atau ditulis tangan dalam
bentuk paling kecil, sehingga tidak ada data pribadi di dalam tes.
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Tes mesin tahap 1 memakai endpoint sinkron /api/convert yang tidak dipasang di produksi.
os.environ["KONVERSIKU_SYNC_ENDPOINT"] = "1"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_report_header(config):
    from konversiku import tools

    found = tools.detect()
    return [
        f"KonversiKu alat sistem: pandoc={found.pandoc_version or 'TIDAK ADA'} "
        f"(rts={' '.join(found.pandoc_rts) or '-'}, reader={','.join(sorted(found.pandoc_readers)) or '-'}, "
        f"writer={','.join(sorted(found.pandoc_writers)) or '-'}), "
        f"libreoffice={found.libreoffice_version or 'TIDAK ADA'}",
    ]


@pytest.fixture(scope="session")
def app_module():
    import app as app_module  # noqa: WPS433

    return app_module


@pytest.fixture()
def client(app_module):
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def formats(app_module):
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as test_client:
        return test_client.get("/api/formats").json()


def require_pair(formats: dict, source: str, target: str) -> None:
    """Lewati (bukan lulus) bila pasangan dinonaktifkan karena alat tidak terpasang."""
    if target not in formats["pairs"].get(source, []):
        reason = formats["disabled"].get(source, {}).get(target, "tidak aktif")
        pytest.skip(f"BELUM DIUJI: {source}->{target} nonaktif ({reason})")


@pytest.fixture()
def require_pandoc(formats):
    require_pair(formats, "md", "html")


@pytest.fixture()
def require_libreoffice(formats):
    require_pair(formats, "docx", "pdf")


def convert(client, source: str, target: str, data: bytes, **params):
    query = {"source": source, "target": target, **params}
    return client.post("/api/convert", params=query, content=data,
                       headers={"Content-Type": "application/octet-stream"})


def notes_of(response) -> list[str]:
    raw = response.headers.get("x-konversiku-notes")
    return json.loads(raw) if raw else []


# ---------------------------------------------------------------- pembuat file

def make_docx(paragraphs=("Halo dunia",), heading: str | None = None,
              table: list[list[str]] | None = None) -> bytes:
    from docx import Document

    document = Document()
    if heading:
        document.add_heading(heading, level=1)
    for text in paragraphs:
        document.add_paragraph(text)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def rtf_escape(text: str) -> str:
    out = []
    for char in text:
        if char in "\\{}":
            out.append("\\" + char)
        elif ord(char) > 127:
            code = ord(char)
            out.append(f"\\u{code if code < 32768 else code - 65536}?")
        else:
            out.append(char)
    return "".join(out)


def make_rtf(heading: str, paragraph: str) -> bytes:
    body = (r"{\rtf1\ansi\deff0{\fonttbl{\f0 Helvetica;}}\f0"
            rf"\pard\b {rtf_escape(heading)}\b0\par"
            rf"\pard {rtf_escape(paragraph)}\par}}")
    return body.encode("ascii")


def rewrite_zip(data: bytes, replace: dict[str, bytes] | None = None,
                add: dict[str, bytes] | None = None) -> bytes:
    replace = replace or {}
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info.filename, replace.get(info.filename, src.read(info.filename)))
        for name, payload in (add or {}).items():
            dst.writestr(name, payload)
    return out.getvalue()


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_text_pdf(pages: list[list[str]]) -> bytes:
    """PDF minimal dengan text layer nyata (font Helvetica, teks ASCII)."""
    objects: list[bytes] = []
    page_count = len(pages)
    font_id = 3 + 2 * page_count
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(page_count))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    for index, lines in enumerate(pages):
        content_id = 4 + 2 * index
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode())
        ops = " ".join(f"({_pdf_escape(line)}) Tj T*" for line in lines)
        stream = f"BT /F1 12 Tf 72 720 Td 16 TL {ops} ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_blank_pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def make_encrypted_pdf() -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_text_pdf([["Rahasia"]]))))
    writer.encrypt(user_password="kunci", owner_password="pemilik")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ------------------------------------------------------ contoh dokumen per format

TITLE = "Judul Utama"
BODY = "Paragraf kedua"
UNICODE = "Ñandú"

DOC_SAMPLES = {
    "txt": f"{TITLE}\n\n{BODY} {UNICODE} dengan <b>bukan tag</b> & simbol.\n".encode(),
    "md": f"# {TITLE}\n\n{BODY} {UNICODE} dengan **tebal**.\n".encode(),
    "html": f"<h1>{TITLE}</h1><p>{BODY} {UNICODE} dengan <b>tebal</b>.</p>".encode(),
    "rst": f"{TITLE}\n{'=' * len(TITLE)}\n\n{BODY} {UNICODE} dengan **tebal**.\n".encode(),
    "tex": ("\\documentclass{article}\n\\begin{document}\n"
            f"\\section{{{TITLE}}}\n{BODY} {UNICODE} dengan \\textbf{{tebal}}.\n\\end{{document}}\n").encode(),
    "rtf": make_rtf(TITLE, f"{BODY} {UNICODE} dengan tebal."),
}


def doc_sample(source: str) -> bytes:
    if source == "docx":
        return make_docx(heading=TITLE, paragraphs=[f"{BODY} {UNICODE} dengan tebal."])
    if source == "pdf":
        return make_text_pdf([[TITLE, f"{BODY} dengan teks."]])
    return DOC_SAMPLES[source]


# ------------------------------------------------------------- parser output

class HtmlText(HTMLParser):
    def __init__(self, data: str):
        super().__init__()
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str | None]] = []
        self.parts: list[str] = []
        self.feed(data)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(attrs)

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())

    @property
    def text(self) -> str:
        return " ".join(self.parts)


def pandoc_plain(data: bytes, reader: str) -> str:
    from konversiku import tools

    found = tools.detect()
    result = subprocess.run([found.pandoc, "--sandbox", "-f", reader, "-t", "plain", "--wrap=none"],
                            input=data, capture_output=True, check=True, timeout=60)
    return result.stdout.decode("utf-8")


def text_of(target: str, data: bytes) -> str:
    """Parse output dengan parser yang sesuai formatnya dan kembalikan teksnya."""
    if target == "txt":
        return data.decode("utf-8")
    if target == "md":
        from markdown_it import MarkdownIt

        tokens = MarkdownIt("commonmark").parse(data.decode("utf-8"))
        return " ".join(t.content for t in tokens if t.type == "inline")
    if target == "html":
        return HtmlText(data.decode("utf-8")).text
    if target == "rst":
        from docutils.core import publish_doctree

        tree = publish_doctree(data.decode("utf-8"),
                               settings_overrides={"report_level": 5, "halt_level": 5,
                                                   "file_insertion_enabled": False, "raw_enabled": False})
        return tree.astext()
    if target == "tex":
        from pylatexenc.latex2text import LatexNodes2Text

        text = data.decode("utf-8")
        assert "\\documentclass" in text and "\\begin{document}" in text
        return LatexNodes2Text().latex_to_text(text)
    if target == "rtf":
        assert data.startswith(b"{\\rtf1")
        return pandoc_plain(data, "rtf")
    if target == "docx":
        from docx import Document

        document = Document(io.BytesIO(data))
        cells = [c.text for t in document.tables for r in t.rows for c in r.cells]
        return " ".join([p.text for p in document.paragraphs] + cells)
    if target == "pdf":
        # pypdfium2, bukan pypdf: pypdf salah membaca huruf beraksen pada font subset
        # TrueType buatan LibreOffice (Cambria), lihat PETUNJUK.md.
        import pypdfium2 as pdfium

        assert data.startswith(b"%PDF")
        document = pdfium.PdfDocument(data)
        try:
            return " ".join(document[i].get_textpage().get_text_range() for i in range(len(document)))
        finally:
            document.close()
    raise ValueError(target)


# ------------------------------------------------------------- contoh data

RECORDS = [
    {"kode": "001", "nama": "Budi, S.T.", "catatan": "baris1\nbaris2"},
    {"kode": "002", "nama": UNICODE, "catatan": "a < b & c"},
]


def _xml_records(records) -> bytes:
    from xml.sax.saxutils import escape

    items = "".join("<item>" + "".join(f"<{k}>{escape(v)}</{k}>" for k, v in r.items()) + "</item>"
                    for r in records)
    return f'<?xml version="1.0" encoding="utf-8"?><data>{items}</data>'.encode()


def _delimited(records, delimiter: str) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(records[0]), delimiter=delimiter)
    writer.writeheader()
    writer.writerows(records)
    return out.getvalue().encode("utf-8")


def data_sample(source: str) -> bytes:
    import yaml

    if source == "csv":
        return _delimited(RECORDS, ",")
    if source == "tsv":
        return _delimited(RECORDS, "\t")
    if source == "json":
        return json.dumps(RECORDS, ensure_ascii=False).encode()
    if source == "yaml":
        return yaml.safe_dump(RECORDS, allow_unicode=True, sort_keys=False).encode()
    if source == "xml":
        return _xml_records(RECORDS)
    raise ValueError(source)


def unwrap(value):
    """Buka pembungkus objek satu-kunci sampai ketemu list (aturan baris tabel)."""
    while isinstance(value, dict) and len(value) == 1:
        value = next(iter(value.values()))
    return value


def records_of(target: str, data: bytes):
    import yaml
    from defusedxml import ElementTree

    text = data.decode("utf-8")
    if target in ("csv", "tsv"):
        delimiter = "," if target == "csv" else "\t"
        return [dict(r) for r in csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)]
    if target == "json":
        return unwrap(json.loads(text))
    if target == "yaml":
        return unwrap(yaml.safe_load(text))
    if target == "xml":
        root = ElementTree.fromstring(data, forbid_dtd=True)
        return [{child.tag: child.text or "" for child in item} for item in root]
    raise ValueError(target)
