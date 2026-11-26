"""Batas ukuran, header, ZIP berbahaya, resource eksternal, sanitasi, timeout, file sementara."""
import http.server
import io
import threading
import zipfile
from pathlib import Path

import pytest

from conftest import convert, doc_sample, make_docx, require_pair, rewrite_zip


# ------------------------------------------------------------ batas umum

def test_empty_upload_rejected(client):
    response = convert(client, "csv", "json", b"")
    assert response.status_code == 400 and "kosong" in response.json()["detail"]
    assert response.headers["cache-control"] == "no-store"


def test_upload_limit_follows_source_limit(client, monkeypatch):
    from konversiku import limits

    monkeypatch.setitem(limits.MAX_UPLOAD_BYTES_BY_SOURCE, "txt", 1000)
    assert convert(client, "txt", "html", b"a" * 1000).status_code == 200
    response = convert(client, "txt", "html", b"a" * 1001)
    assert response.status_code == 413 and "TXT" in response.json()["detail"]


def test_upload_limit_message_shows_mib_and_mb(client, monkeypatch):
    from konversiku import limits

    monkeypatch.setitem(limits.MAX_UPLOAD_BYTES_BY_SOURCE, "txt", 2 * limits.MiB)
    response = convert(client, "txt", "html", b"a" * (2 * limits.MiB + 1))
    assert response.status_code == 413
    assert response.json()["detail"] == "File TXT melebihi batas 2 MiB (2,1 MB)."


def test_download_headers(client):
    response = convert(client, "txt", "html", b"<script>alert(1)</script>")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'attachment; filename="hasil.html"'
    assert "sandbox" in response.headers["content-security-policy"]
    assert b"<script" not in response.content


def test_home_page_headers(client):
    response = client.get("/")
    assert response.status_code == 200 and response.headers["x-content-type-options"] == "nosniff"


def test_server_busy_returns_503(client, monkeypatch):
    from konversiku import limits

    monkeypatch.setattr(limits, "MAX_PENDING", 0)
    response = convert(client, "csv", "json", b"a\n1\n")
    assert response.status_code == 503 and "sibuk" in response.json()["detail"]


def test_output_limit(client, monkeypatch):
    from konversiku import limits

    monkeypatch.setattr(limits, "MAX_OUTPUT_BYTES", 10)
    response = convert(client, "csv", "json", b"a\nnilai yang cukup panjang\n")
    assert response.status_code == 400 and "terlalu besar" in response.json()["detail"]


# ------------------------------------------------------------ ZIP DOCX

def test_docx_zip_bomb_rejected(client, monkeypatch):
    from konversiku import limits

    monkeypatch.setattr(limits, "MAX_ZIP_EXPANDED_BYTES", 20 * 1024 * 1024)
    bomb = rewrite_zip(make_docx(), add={"word/media/bom.bin": b"\0" * (21 * 1024 * 1024)})
    response = convert(client, "docx", "txt", bomb)
    assert response.status_code == 400 and "20 MiB (21 MB)" in response.json()["detail"]


def test_docx_text_xml_limit(client, monkeypatch):
    """Ukuran file DOCX tidak mencerminkan isi: XML teks yang dibuka dibatasi tersendiri."""
    from konversiku import limits

    small = make_docx(paragraphs=["Isi pendek"])
    big = make_docx(paragraphs=["Paragraf panjang berisi teks. " * 40] * 20)
    with zipfile.ZipFile(io.BytesIO(small)) as archive:
        xml_now = sum(i.file_size for i in archive.infolist() if i.filename.endswith((".xml", ".rels")))
    monkeypatch.setattr(limits, "MAX_DOCX_XML_BYTES", xml_now + 8000)
    assert convert(client, "docx", "txt", small).status_code == 200
    for target in ("txt", "md", "pdf"):
        response = convert(client, "docx", target, big)
        assert response.status_code == 400, target
        assert "teks DOCX terlalu besar" in response.json()["detail"]


def _renamed_main_part() -> bytes:
    """Isi utama di word/main.xml (ditemukan lewat _rels/.rels), word/document.xml hanya rintisan."""
    data = make_docx(paragraphs=["Paragraf panjang berisi teks. " * 40] * 20)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        body = archive.read("word/document.xml")
        rels = archive.read("_rels/.rels").decode()
        types = archive.read("[Content_Types].xml").decode()
        body_rels = archive.read("word/_rels/document.xml.rels")
    assert 'Target="word/document.xml"' in rels and 'PartName="/word/document.xml"' in types
    with zipfile.ZipFile(io.BytesIO(make_docx(paragraphs=["rintisan"]))) as archive:
        stub = archive.read("word/document.xml")
    return rewrite_zip(data, replace={
        "_rels/.rels": rels.replace('Target="word/document.xml"', 'Target="word/main.xml"').encode(),
        "[Content_Types].xml": types.replace('PartName="/word/document.xml"', 'PartName="/word/main.xml"').encode(),
        "word/document.xml": stub,
    }, add={"word/main.xml": body, "word/_rels/main.xml.rels": body_rels})


def test_docx_renamed_main_part_rejected(client):
    """Bagian utama yang dipindah ke nama lain akan dibaca python-docx/Pandoc lewat relasi,
    sehingga lolos dari pemeriksaan berbasis nama. DOCX seperti itu ditolak."""
    for target in ("txt", "md"):
        response = convert(client, "docx", target, _renamed_main_part())
        assert response.status_code == 400, target
        assert "word/document.xml" in response.json()["detail"]


def test_docx_xml_limit_counts_parts_by_content_not_name(client, monkeypatch):
    from konversiku import limits

    base = make_docx(paragraphs=["Isi pendek"])
    with zipfile.ZipFile(io.BytesIO(base)) as archive:
        xml_now = sum(i.file_size for i in archive.infolist() if i.filename.endswith((".xml", ".rels")))
    monkeypatch.setattr(limits, "MAX_DOCX_XML_BYTES", xml_now + 5000)
    assert convert(client, "docx", "txt", base).status_code == 200
    disguised = rewrite_zip(base, add={"word/media/image9.png": b"<x>" + b"a" * 6000 + b"</x>"})
    response = convert(client, "docx", "txt", disguised)
    assert response.status_code == 400 and "teks DOCX terlalu besar" in response.json()["detail"]
    image = rewrite_zip(base, add={"word/media/image9.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 6000})
    assert convert(client, "docx", "txt", image).status_code == 200


def test_docx_field_in_other_xml_part_rejected(client):
    field = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
             '<w:p><w:fldSimple w:instr=" INCLUDETEXT &quot;file:///etc/passwd&quot; "/></w:p>'
             '</w:body></w:document>')
    evil = rewrite_zip(make_docx(paragraphs=["isi"]), add={"word/glossary/document.xml": field.encode()})
    response = convert(client, "docx", "txt", evil)
    assert response.status_code == 400 and "INCLUDETEXT" in response.json()["detail"]


def test_docx_entry_count_rejected(client):
    many = rewrite_zip(make_docx(), add={f"word/x/{i}.xml": b"" for i in range(1001)})
    response = convert(client, "docx", "txt", many)
    assert response.status_code == 400 and "1.000" in response.json()["detail"]


def test_docx_path_traversal_entry_rejected(client):
    evil = rewrite_zip(make_docx(), add={"../../keluar.txt": b"x"})
    response = convert(client, "docx", "txt", evil)
    assert response.status_code == 400 and "nama entri" in response.json()["detail"]


def _with_relationship(target_type: str, target: str) -> bytes:
    data = make_docx(paragraphs=["isi"])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rels = archive.read("word/_rels/document.xml.rels").decode()
    rels = rels.replace("</Relationships>", (
        f'<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/{target_type}" Target="{target}" TargetMode="External"/></Relationships>'))
    return rewrite_zip(data, replace={"word/_rels/document.xml.rels": rels.encode()})


def test_docx_external_relationship_rejected(client):
    response = convert(client, "docx", "pdf", _with_relationship("image", "http://127.0.0.1:9/x.png"))
    assert response.status_code == 400 and "eksternal" in response.json()["detail"]


def test_docx_external_hyperlink_allowed(client):
    assert convert(client, "docx", "txt", _with_relationship("hyperlink", "https://contoh.test")).status_code == 200


def test_docx_includetext_field_rejected(client):
    data = make_docx(paragraphs=["isi"])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode()
    field = ('<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
             '<w:r><w:instrText xml:space="preserve"> INCLUDE</w:instrText></w:r>'
             '<w:r><w:instrText>TEXT "file:///etc/passwd" </w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
    evil = rewrite_zip(data, replace={"word/document.xml": xml.replace("<w:sectPr", field + "<w:sectPr", 1).encode()})
    response = convert(client, "docx", "pdf", evil)
    assert response.status_code == 400 and "INCLUDETEXT" in response.json()["detail"]


# ------------------------------------------- resource eksternal dan file lokal

class _Recorder(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        body = b"<p>RAHASIA-DARI-URL</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def recorder():
    _Recorder.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", _Recorder.hits
    server.shutdown()


@pytest.fixture()
def secret(tmp_path):
    path = tmp_path / "rahasia.txt"
    path.write_text("RAHASIA-FILE-LOKAL", encoding="utf-8")
    return path


def _external_payload(source: str, base: str, secret_path: Path) -> bytes:
    uri, plain = secret_path.as_uri(), secret_path.as_posix()
    if source == "html":
        return (f"<link rel='stylesheet' href='{base}/gaya.css'><p>teks aman</p>"
                f"<img src='{base}/g.png' alt='luar'><img src='{uri}' alt='lokal'>"
                f"<iframe src='{base}/frame.html'></iframe><iframe src='{uri}'></iframe>").encode()
    if source == "md":
        return f"teks aman\n\n![luar]({base}/g.png)\n\n![lokal]({uri})\n\n<iframe src='{base}/f'></iframe>\n".encode()
    if source == "rst":
        return (f"teks aman\n\n.. include:: {plain}\n\n.. image:: {base}/g.png\n\n"
                f".. raw:: html\n\n   <iframe src='{base}/f'></iframe>\n").encode()
    if source == "tex":
        return ("\\documentclass{article}\\usepackage{graphicx}\\begin{document}teks aman "
                f"\\input{{{plain}}} \\include{{{plain}}} \\includegraphics{{{base}/g.png}}"
                "\\end{document}").encode()
    raise ValueError(source)


EXTERNAL_CASES = [(s, t) for s in ("html", "md", "rst", "tex") for t in ("txt", "docx", "html", "pdf") if s != t]


@pytest.mark.parametrize("source, target", EXTERNAL_CASES, ids=[f"{s}-{t}" for s, t in EXTERNAL_CASES])
def test_documents_never_fetch_urls_or_local_files(client, formats, recorder, secret, source, target):
    require_pair(formats, source, target)
    base, hits = recorder
    response = convert(client, source, target, _external_payload(source, base, secret))
    assert response.status_code == 200, response.text
    from conftest import text_of

    raw = response.content
    blob = raw.decode("utf-8", "replace")
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            blob = "\n".join(archive.read(n).decode("utf-8", "replace") for n in archive.namelist())
    blob += text_of(target, raw)
    assert hits == []
    assert "RAHASIA-FILE-LOKAL" not in blob and "RAHASIA-DARI-URL" not in blob
    assert "teks aman" in blob


@pytest.mark.parametrize("source, target, payload", [
    ("md", "html", b"# x\n\n<script>alert(1)</script>\n\n[klik](javascript:alert(1))\n\n<img src=x onerror=alert(1)>\n"),
    ("html", "md", b"<p onclick='alert(1)'>a</p><script>alert(1)</script><a href='javascript:alert(1)'>k</a>"),
    ("rst", "html", b"x\n\n.. raw:: html\n\n   <script>alert(1)</script>\n\n`k <javascript:alert(1)>`_\n"),
    ("tex", "html", b"\\documentclass{article}\\begin{document}\\href{javascript:alert(1)}{k}\\end{document}"),
])
def test_active_content_removed(client, formats, source, target, payload):
    require_pair(formats, source, target)
    response = convert(client, source, target, payload)
    assert response.status_code == 200, response.text
    lowered = response.content.decode().lower()
    for marker in ("<script", "javascript:", "onerror", "onclick"):
        assert marker not in lowered


# --------------------------------------------------- timeout dan file sementara

@pytest.fixture()
def work(tmp_path, monkeypatch):
    folder = tmp_path / "kerja"
    folder.mkdir()
    monkeypatch.setenv("KONVERSIKU_TMPDIR", str(folder))
    return folder


def leftovers(folder: Path):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def test_temp_cleanup_after_success(client, formats, work):
    require_pair(formats, "md", "pdf")
    assert convert(client, "md", "pdf", doc_sample("md")).status_code == 200
    assert leftovers(work) == []


def test_temp_cleanup_after_error(client, formats, work, monkeypatch):
    require_pair(formats, "md", "docx")
    from konversiku import limits

    monkeypatch.setattr(limits, "MAX_OUTPUT_BYTES", 10)
    response = convert(client, "md", "docx", b"# judul yang cukup panjang\n")
    assert response.status_code == 400
    assert leftovers(work) == []


def test_pandoc_timeout_returns_504_and_cleans_up(client, formats, work, monkeypatch):
    require_pair(formats, "md", "html")
    from konversiku import limits

    monkeypatch.setattr(limits, "PANDOC_TIMEOUT", 0.001)
    response = convert(client, "md", "html", b"# x\n")
    assert response.status_code == 504 and "batas waktu" in response.json()["detail"]
    assert leftovers(work) == []


def test_pandoc_heap_exhaustion_gives_clear_message(formats):
    require_pair(formats, "md", "html")
    from konversiku import tools
    from konversiku.common import ConversionError

    found = tools.detect()
    big = ("## Bagian\n\n- a\n- b\n\n" * 20_000).encode()
    with pytest.raises(ConversionError) as caught:
        tools.run([found.pandoc, "+RTS", "-M4M", "-RTS", "--sandbox", "-f", "gfm", "-t", "json"],
                  timeout=60, input_bytes=big)
    assert caught.value.status_code == 413
    assert "memori" in caught.value.message and "Pecah" in caught.value.message


def test_libreoffice_timeout_recovers(client, formats, work, monkeypatch):
    require_pair(formats, "docx", "pdf")
    from konversiku import limits

    monkeypatch.setattr(limits, "LIBREOFFICE_TIMEOUT", 0.2)
    response = convert(client, "docx", "pdf", make_docx())
    assert response.status_code == 504
    assert leftovers(work) == []
    monkeypatch.setattr(limits, "LIBREOFFICE_TIMEOUT", 90)
    assert convert(client, "docx", "pdf", make_docx()).status_code == 200
