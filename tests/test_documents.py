"""Dokumen: setiap pasangan diuji input nyata -> API -> parse output dengan parser yang sesuai."""
import io

import pytest
from docx import Document

from conftest import (BODY, TITLE, UNICODE, HtmlText, convert, doc_sample, make_blank_pdf,
                      make_docx, make_encrypted_pdf, make_text_pdf, notes_of, require_pair, text_of)

DOCS = ["txt", "md", "rtf", "html", "rst", "tex"]
DOC_PAIRS = ([(s, t) for s in DOCS for t in DOCS + ["docx", "pdf"] if s != t]
             + [("docx", t) for t in DOCS + ["pdf"]]
             + [("pdf", t) for t in ("txt", "md", "docx")])


def test_document_pair_count():
    assert len(DOC_PAIRS) == 52


@pytest.mark.parametrize("source, target", DOC_PAIRS, ids=[f"{s}-{t}" for s, t in DOC_PAIRS])
def test_document_pair_converts_and_output_parses(client, formats, source, target):
    require_pair(formats, source, target)
    response = convert(client, source, target, doc_sample(source))
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == f'attachment; filename="hasil.{target}"'
    # casefold: pylatexenc merender \section sebagai huruf kapital ("§ JUDUL UTAMA")
    text = " ".join(text_of(target, response.content).split()).casefold()
    assert TITLE.casefold() in text, text[-300:]
    assert BODY.casefold() in text, text[-300:]
    if source != "pdf":           # PDF contoh hanya berisi teks ASCII (Helvetica)
        assert UNICODE.casefold() in text, text[-300:]


# ------------------------------------------------------------ struktur penting

def test_md_to_html_keeps_structure(client, formats):
    require_pair(formats, "md", "html")
    response = convert(client, "md", "html", b"# Judul\n\nTeks **tebal** [x](https://contoh.test)\n\n- a\n- b\n")
    page = HtmlText(response.content.decode())
    assert {"h1", "strong", "a", "ul", "li"} <= set(page.tags)
    assert ("href", "https://contoh.test") in page.attrs


def test_rst_to_md_keeps_heading(client, formats):
    require_pair(formats, "rst", "md")
    from markdown_it import MarkdownIt

    response = convert(client, "rst", "md", doc_sample("rst"))
    tokens = MarkdownIt("commonmark").parse(response.content.decode())
    assert any(t.type == "heading_open" for t in tokens)


def test_tex_to_docx_heading_style(client, formats):
    require_pair(formats, "tex", "docx")
    response = convert(client, "tex", "docx", doc_sample("tex"))
    styles = [(p.style.name, p.text) for p in Document(io.BytesIO(response.content)).paragraphs]
    assert ("Heading 1", TITLE) in styles


def test_docx_to_rst_parses_with_docutils(client, formats):
    require_pair(formats, "docx", "rst")
    from docutils.core import publish_doctree

    response = convert(client, "docx", "rst", make_docx(heading="Laporan", paragraphs=["Isi"],
                                                         table=[["kode", "nama"], ["001", "Ani"]]))
    tree = publish_doctree(response.content.decode(), settings_overrides={"report_level": 5})
    assert tree.next_node(lambda n: n.tagname == "title").astext() == "Laporan"
    assert "001" in tree.astext()


def test_txt_to_md_escapes_markdown(client, formats):
    require_pair(formats, "txt", "md")
    from markdown_it import MarkdownIt

    response = convert(client, "txt", "md", b"*bukan miring* # [x](y) <b>tag</b>\n")
    rendered = MarkdownIt("commonmark").render(response.content.decode())
    assert "<em>" not in rendered and "<a " not in rendered and "&lt;b&gt;" in rendered


def test_txt_to_docx_legacy_one_paragraph_per_line(client):
    response = convert(client, "txt", "docx", "baris satu Ñ\nbaris dua".encode())
    assert [p.text for p in Document(io.BytesIO(response.content)).paragraphs] == ["baris satu Ñ", "baris dua"]


def test_txt_to_docx_many_short_lines_is_linear(client):
    """python-docx add_paragraph mencari sectPr setiap kali (kuadratik); TXT berbaris pendek
    sebesar batas harus tetap selesai cepat dengan satu paragraf per baris."""
    import time

    lines = [f"baris {i}\tke-{i}" for i in range(60_000)]
    start = time.monotonic()
    response = convert(client, "txt", "docx", "\n".join(lines).encode())
    elapsed = time.monotonic() - start
    assert response.status_code == 200, response.text
    paragraphs = Document(io.BytesIO(response.content)).paragraphs
    assert len(paragraphs) == 60_000
    assert (paragraphs[0].text, paragraphs[-1].text) == (lines[0], lines[-1])
    assert elapsed < 15, f"{elapsed:.1f} s"


def test_txt_to_docx_keeps_blank_lines_and_tabs(client):
    response = convert(client, "txt", "docx", b"a\tb\r\n\nc")
    assert [p.text for p in Document(io.BytesIO(response.content)).paragraphs] == ["a\tb", "", "c"]


def test_docx_to_txt_legacy_tables_tab_separated(client):
    response = convert(client, "docx", "txt", make_docx(heading="Laporan", paragraphs=["Isi Ñ"],
                                                         table=[["kode", "nama"], ["001", "Ani"]]))
    assert response.content.decode().splitlines() == ["Laporan", "Isi Ñ", "kode\tnama", "001\tAni"]


def test_pdf_target_notes_explain_pipeline(client, formats):
    require_pair(formats, "md", "pdf")
    response = convert(client, "md", "pdf", doc_sample("md"))
    assert any("LibreOffice" in note for note in notes_of(response))


# ------------------------------------------------------------ PDF input

@pytest.mark.parametrize("target", ["txt", "md", "docx"])
def test_pdf_without_text_layer_rejected(client, formats, target):
    require_pair(formats, "pdf", target)
    response = convert(client, "pdf", target, make_blank_pdf())
    assert response.status_code == 400
    assert "text layer" in response.json()["detail"] and "OCR" in response.json()["detail"]


def test_pdf_encrypted_rejected(client):
    response = convert(client, "pdf", "txt", make_encrypted_pdf())
    assert response.status_code == 400
    assert "terenkripsi" in response.json()["detail"]


def test_pdf_corrupt_and_fake_rejected(client):
    assert "PDF" in convert(client, "pdf", "txt", b"%PDF-1.4 rusak").json()["detail"]
    response = convert(client, "pdf", "txt", b"ini teks biasa, bukan pdf")
    assert response.status_code == 400 and "bukan PDF" in response.json()["detail"]


def test_pdf_page_limit(client, monkeypatch):
    from konversiku import limits

    monkeypatch.setattr(limits, "MAX_PDF_PAGES", 100)
    response = convert(client, "pdf", "txt", make_text_pdf([["x"]] * 101))
    assert response.status_code == 400
    assert "100 halaman" in response.json()["detail"]


def test_pdf_to_docx_paragraphs(client):
    response = convert(client, "pdf", "docx", make_text_pdf([["Halaman satu"], ["Halaman dua"]]))
    texts = [p.text for p in Document(io.BytesIO(response.content)).paragraphs]
    assert "Halaman satu" in texts and "Halaman dua" in texts


# ------------------------------------------------- validasi isi, bukan ekstensi

@pytest.mark.parametrize("source, payload, fragment", [
    ("rtf", b"ini bukan rtf", "RTF"),
    ("html", b"hanya teks tanpa tag", "HTML"),
    ("docx", b"bukan zip", ".docx"),
    ("txt", b"\x00\x01\x02biner", "teks"),
    ("md", "café".encode("cp1252"), "UTF-8"),
    ("tex", b"PK\x03\x04" + b"\x00" * 20, "teks"),
])
def test_content_is_validated(client, formats, source, payload, fragment):
    target = "txt" if source != "txt" else "docx"
    require_pair(formats, source, target)
    response = convert(client, source, target, payload)
    assert response.status_code == 400, response.text
    assert fragment in response.json()["detail"]


def test_docx_without_text_rejected(client):
    response = convert(client, "docx", "txt", make_docx(paragraphs=[]))
    assert response.status_code == 400
    assert "teks" in response.json()["detail"]


def test_markdown_and_htm_aliases(client, formats):
    require_pair(formats, "md", "txt")
    assert convert(client, "markdown", "txt", b"# x\n").status_code == 200
    assert convert(client, "htm", "txt", b"<p>x</p>").status_code == 200
