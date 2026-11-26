"""Uji browser nyata (Playwright + Chrome) terhadap stack tahap 2 sungguhan.

Server: API dev di VPS (uvicorn + pekerja Celery + PostgreSQL + Redis) yang dijangkau lewat
tunnel SSH, alamatnya di ``KONVERSIKU_E2E_URL`` (mis. ``http://127.0.0.1:8001``).
Catatan jujur: drag and drop di sini adalah event drop sintetis (DataTransfer dibuat
di halaman). Drag native dari Windows Explorer tidak diuji otomatis.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "laporan_tes" / "screenshots"
# Log jaringan bawaan Chrome untuk respons 4xx/5xx atau request yang sengaja diputus.
NETWORK_ERROR = "Failed to load resource:"
STORE = "konversiku.tugas"

sys.path.insert(0, str(ROOT / "tests"))
from conftest import doc_sample, make_docx, text_of  # noqa: E402


@pytest.fixture(scope="session")
def server():
    url = os.environ.get("KONVERSIKU_E2E_URL", "").rstrip("/")
    if not url:
        pytest.skip("BELUM DIUJI: KONVERSIKU_E2E_URL belum diisi (API dev lewat tunnel SSH)")
    try:
        with urllib.request.urlopen(url + "/api/formats", timeout=10) as response:
            assert response.status == 200
    except OSError as exc:
        pytest.fail(f"API dev di {url} tidak terjangkau: {exc}")
    return url


@pytest.fixture(scope="session")
def browser():
    channel = os.environ.get("KONVERSIKU_E2E_CHANNEL", "chrome" if sys.platform == "win32" else "")
    with sync_api.sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(channel=channel or None)
        except sync_api.Error as exc:
            pytest.skip(f"BELUM DIUJI: browser untuk E2E tidak bisa dijalankan ({exc})")
        yield instance
        instance.close()


@pytest.fixture(scope="session")
def published(server):
    """Batas yang diumumkan server (/api/formats), sumber angka yang ditampilkan halaman."""
    with urllib.request.urlopen(server + "/api/formats", timeout=10) as response:
        return json.load(response)["limits"]


def _mib(value: int) -> str:
    """Teks ukuran yang harus sama dengan mib() di halaman dan pesan server: "25 MiB (26,2 MB)"."""
    from konversiku import limits

    return limits.size_label(value)


def _is_result(url: str) -> bool:
    return "/api/jobs/hasil" in url


def _is_submit(url: str) -> bool:
    return "/api/jobs?" in url


class View:
    def __init__(self, browser, url, width=1024, height=800):
        self.context = browser.new_context(accept_downloads=True, viewport={"width": width, "height": height})
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.allowed_network_errors = 0
        self.closed = False
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("dialog", lambda d: self.errors.append(f"dialog: {d.message}"))
        self.url = url
        # Chrome yang dikendalikan Playwright mengubah respons fetch() berisi PDF dengan
        # "Content-Disposition: attachment" menjadi 204 kosong (intersepsi unduhan Playwright;
        # Chromium biasa menerima 200 utuh). Respons server diteruskan APA ADANYA (status,
        # header attachment, isi) lewat route.fetch() agar alur UI tetap teruji dengan data asli.
        self.page.route(_is_result, self._passthrough)
        self.open()

    @staticmethod
    def _passthrough(route):
        route.fulfill(response=route.fetch(timeout=180_000))

    def open(self):
        self.page.goto(self.url)
        self.page.wait_for_selector('#fileInput[accept*=".rst"]', state="attached")

    def choose(self, name, data, mime="application/octet-stream"):
        with self.page.expect_file_chooser() as chooser:
            self.page.click("#pick")
        chooser.value.set_files(files=[{"name": name, "mimeType": mime, "buffer": data}])

    def targets(self):
        return self.page.eval_on_selector_all("#targets [data-format]", "els => els.map(e => e.dataset.format)")

    def submit(self) -> str:
        """Klik Konversi, tunggu kode tugas tampil, kembalikan kodenya."""
        self.page.click("#convert")
        self.page.wait_for_selector("#jobBox:not([hidden])", timeout=60_000)
        return self.page.text_content("#jobCode").strip()

    def convert_and_download(self):
        self.submit()
        self.page.wait_for_selector("#download:not([hidden])", timeout=180_000)
        with self.page.expect_download() as info:
            self.page.click("#download")
        return Path(info.value.path()).read_bytes(), info.value.suggested_filename

    def wait_status(self, text: str, timeout=180_000):
        self.page.wait_for_function("t => document.querySelector('#status').textContent.includes(t)",
                                    arg=text, timeout=timeout)

    def status(self):
        return self.text("#status")

    def text(self, selector):
        """Teks elemen dengan spasi tak-putus (dipakai di ukuran "25 MiB (26,2 MB)") jadi spasi biasa."""
        return (self.page.text_content(selector) or "").replace("\u00a0", " ")

    def notes(self):
        return self.page.text_content("#notes") or ""

    def shot(self, name):
        SHOTS.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)

    def close(self):
        if not self.closed:
            self.closed = True
            self.context.close()
        network = [e for e in self.errors if e.startswith(NETWORK_ERROR)]
        others = [e for e in self.errors if not e.startswith(NETWORK_ERROR)]
        assert others == [], others
        assert len(network) <= self.allowed_network_errors, network


@pytest.fixture()
def ui(browser, server):
    views = []

    def make(width=1024, height=800, url=None):
        view = View(browser, url or server, width, height)
        views.append(view)
        return view

    yield make
    for view in views:
        view.close()


def _fake_formats(view, server, change):
    real = json.loads(urllib.request.urlopen(server + "/api/formats").read())
    change(real)
    view.page.route("**/api/formats", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(real)))
    view.open()


CSV = 'kode,nama\r\n001,"Budi, S.T."\r\n002,Ñandú\r\n'.encode()
CSV_RECORDS = [{"kode": "001", "nama": "Budi, S.T."}, {"kode": "002", "nama": "Ñandú"}]
DOC_TARGETS = ["txt", "rtf", "html", "rst", "tex", "docx", "pdf"]


def test_initial_wheel_visible(ui):
    view = ui()
    box = view.page.locator(".wheel").bounding_box()
    assert box["width"] == 280 and box["height"] == 280
    assert view.page.is_visible("#pick") and view.page.text_content("#pick").strip() == "Pilih file"
    assert view.targets() == []
    accept = view.page.get_attribute("#fileInput", "accept")
    for ext in (".txt", ".md", ".markdown", ".rtf", ".html", ".htm", ".rst", ".tex", ".docx", ".pdf",
                ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml"):
        assert ext in accept.split(","), ext
    assert view.page.is_visible("#codeInput")
    view.shot("01_awal_1024")
    view.page.click("#help summary")
    help_text = view.page.text_content("#help")
    assert "MD (Markdown) ke TXT, RTF, HTML, RST, TEX, DOCX, PDF" in help_text
    assert "CSV ke TSV, JSON, YAML, XML" in help_text and "CSV (CSV)" not in help_text
    assert "kode tugas" in help_text and "24 jam" in help_text
    assert "sinkron" not in help_text
    view.shot("02_bantuan")


def test_markdown_two_targets_from_same_input(ui):
    view = ui()
    view.choose("catatan.md", doc_sample("md"), "text/markdown")
    assert view.targets() == DOC_TARGETS
    view.page.click('#targets [data-format="html"]')
    assert "HTML mentah" in view.notes()
    data, name = view.convert_and_download()
    assert name == "catatan_hasil.html" and "Judul Utama" in text_of("html", data)
    view.page.click('#targets [data-format="pdf"]')
    assert "LibreOffice" in view.notes()          # peringatan terlihat sebelum konversi
    data, name = view.convert_and_download()
    assert name == "catatan_hasil.pdf" and "Judul Utama" in text_of("pdf", data)
    assert view.page.locator("#jobList li").count() >= 2   # dua tugas dari satu file
    view.shot("03_md_ke_pdf")


def test_csv_picker_json_then_tsv(ui):
    view = ui()
    view.choose("data.csv", CSV, "text/csv")
    assert view.targets() == ["tsv", "json", "yaml", "xml"]
    view.page.click('#targets [data-format="json"]')
    data, _ = view.convert_and_download()
    assert json.loads(data) == CSV_RECORDS
    view.page.click('#targets [data-format="tsv"]')
    assert view.page.is_visible("#guardField")
    data, _ = view.convert_and_download()
    assert list(csv.reader(io.StringIO(data.decode(), newline=""), delimiter="\t"))[1] == ["001", "Budi, S.T."]


def test_synthetic_drop(ui):
    view = ui()
    handle = view.page.evaluate_handle(
        """text => { const dt = new DataTransfer();
                     dt.items.add(new File([text], 'data.xml', {type: 'application/xml'})); return dt; }""",
        '<data><item><a>1</a></item></data>')
    view.page.dispatch_event("#dropzone", "dragenter", {"dataTransfer": handle})
    view.page.dispatch_event("#dropzone", "drop", {"dataTransfer": handle})
    assert view.targets() == ["csv", "tsv", "json", "yaml"]
    view.page.click('#targets [data-format="json"]')
    assert "@nama" in view.notes()
    data, _ = view.convert_and_download()
    assert json.loads(data) == {"data": {"item": {"a": "1"}}}


def test_drop_rejects_multiple_files(ui):
    view = ui()
    handle = view.page.evaluate_handle(
        """() => { const dt = new DataTransfer(); dt.items.add(new File(['a'], 'a.txt'));
                   dt.items.add(new File(['b'], 'b.txt')); return dt; }""")
    view.page.dispatch_event("#dropzone", "drop", {"dataTransfer": handle})
    assert "satu file" in view.status()


def test_api_error_then_retry(ui):
    view = ui()
    view.allowed_network_errors = 2
    view.choose("tabel.json", b'[{"a": 1, "a": 2}]', "application/json")
    view.page.click('#targets [data-format="csv"]')
    view.submit()
    view.wait_status("duplikat")                 # ditolak pekerja, pesan mesin tampil
    assert view.page.is_enabled("#convert")
    view.choose("tabel.json", b'[{"a": 1}]', "application/json")
    view.page.click('#targets [data-format="csv"]')
    calls = {"n": 0}

    def flaky(route):
        calls["n"] += 1
        route.abort() if calls["n"] == 1 else route.continue_()

    view.page.route(_is_submit, flaky)
    view.page.click("#convert")
    view.wait_status("Koneksi gagal")
    assert view.page.is_enabled("#convert")
    data, _ = view.convert_and_download()
    assert data.decode().splitlines() == ["a", "1"]


def test_site_icons_load(ui):
    """Semua ikon dan manifest yang ditautkan halaman benar-benar terunduh (200, bukan 404).

    Alamat diambil dari halaman, lalu diunduh lewat klien HTTP Playwright: selama page.route()
    aktif (dipasang View untuk unduhan hasil), fetch() di halaman ke /favicon.ico gagal "Failed to
    fetch" di Chrome yang dikendalikan Playwright, padahal tanpa pencegat dan lewat curl hasilnya 200.
    """
    view = ui()
    hrefs = view.page.eval_on_selector_all(
        "link[rel~=icon],link[rel=apple-touch-icon],link[rel=manifest]", "els => els.map(e => e.href)")
    assert len(hrefs) == 5
    statuses = {href: view.page.request.get(href).status for href in hrefs}
    assert all(status == 200 for status in statuses.values()), statuses


def test_client_side_validation(ui, published):
    view = ui()
    view.choose("kosong.md", b"", "text/markdown")
    assert "kosong" in view.status()
    txt_limit = published["max_upload_bytes_per_source"]["txt"]
    view.choose("besar.txt", b"a" * (txt_limit + 1), "text/plain")
    assert view.status() == f"File TXT melebihi batas {_mib(txt_limit)}."
    view.choose("pas.txt", b"a" * txt_limit, "text/plain")
    assert "melebihi" not in view.status()
    view.choose("lama.doc", b"x", "application/msword")
    assert "tidak didukung" in view.status() and "RST" in view.status()
    view.choose("buku.xlsx", b"x")
    assert "tidak didukung" in view.status()
    assert view.targets() == []


def test_per_format_limit_shown(ui, published):
    """Petunjuk awal menyebut batas terbesar; setelah memilih file, batas format itu yang dipakai."""
    per = published["max_upload_bytes_per_source"]
    view = ui()
    hint = view.text("#hint")
    assert _mib(published["max_upload_bytes"]) in hint and "tergantung format" in hint
    view.choose("data.csv", b"a,b\n1,2\n", "text/csv")
    assert f"maks. {_mib(per['csv'])}" in view.text("#filename")
    view.choose("data.yml", b"a: 1\n", "application/yaml")
    assert f"maks. {_mib(per['yaml'])}" in view.text("#filename")
    view.choose("besar.yml", b"a" * (per["yaml"] + 1), "application/yaml")
    assert view.status() == f"File YAML melebihi batas {_mib(per['yaml'])}."
    help_text = view.text("#limitsText")
    for source in ("txt", "docx", "csv", "yaml"):
        assert source.upper() in help_text and _mib(per[source]) in help_text


def test_formula_guard_checkbox(ui):
    view = ui()
    view.choose("rumus.json", json.dumps([{"a": "=1+1"}]).encode(), "application/json")
    view.page.click('#targets [data-format="csv"]')
    assert view.page.is_checked("#guard")
    data, _ = view.convert_and_download()
    assert data.decode().splitlines()[1] == "'=1+1"
    view.page.uncheck("#guard")
    data, _ = view.convert_and_download()
    assert data.decode().splitlines()[1] == "=1+1"


def test_html_result_downloaded_and_names_are_text(ui):
    view = ui()
    evil = "<img src=x onerror=alert(1)>.txt"
    view.choose(evil, b"<script>alert('x')</script>", "text/plain")
    assert evil in view.page.text_content("#filename")
    assert view.page.locator("#filename img").count() == 0
    view.page.click('#targets [data-format="html"]')
    before = view.page.url
    data, name = view.convert_and_download()
    assert view.page.url == before and name.endswith("_hasil.html")
    assert b"<script" not in data and b"&lt;script&gt;" in data
    assert view.page.locator("#jobList img").count() == 0


def test_disabled_pairs_explain_reason(ui, server):
    view = ui()
    reason = "LibreOffice tidak terpasang di server ini, jadi DOCX ke PDF dinonaktifkan."

    def without_libreoffice(formats):
        formats["pairs"]["docx"] = [t for t in formats["pairs"]["docx"] if t != "pdf"]
        formats["disabled"].setdefault("docx", {})["pdf"] = reason

    _fake_formats(view, server, without_libreoffice)
    view.choose("surat.docx", make_docx(paragraphs=["Halo"]))
    pdf = view.page.locator('#targets [data-format="pdf"]')
    assert pdf.get_attribute("aria-disabled") == "true"
    pdf.focus()
    view.page.keyboard.press("Enter")
    assert "LibreOffice" in view.status()
    assert view.page.is_disabled("#convert")
    assert "LibreOffice" in view.page.text_content("#help")
    view.shot("04_pasangan_nonaktif")


def test_keyboard_selects_target(ui):
    view = ui()
    view.choose("data.csv", CSV, "text/csv")
    view.page.focus('#targets [data-format="tsv"]')
    view.page.keyboard.press("Tab")
    assert view.page.evaluate("document.activeElement.dataset.format") == "json"
    view.page.keyboard.press("Space")
    assert view.page.get_attribute('#targets [data-format="json"]', "aria-pressed") == "true"
    assert view.page.is_enabled("#convert")


def test_pagination_when_more_than_eight_targets(ui, server):
    view = ui()
    many = ["tsv", "json", "yaml", "xml", "md", "html", "txt", "rst", "tex", "docx"]

    def many_targets(formats):
        formats["pairs"]["csv"] = many
        formats["notes"]["csv"] = {fmt: [] for fmt in many}

    _fake_formats(view, server, many_targets)
    view.choose("data.csv", CSV, "text/csv")
    assert view.targets() == many[:7] and view.page.is_visible("#targets .more")
    view.page.click("#targets .more")
    assert view.targets() == many[7:]
    view.shot("05_pagination")


@pytest.mark.parametrize("width", [320, 375, 768])
def test_responsive_without_overflow(ui, width):
    view = ui(width=width, height=900)
    view.choose("catatan.md", doc_sample("md"), "text/markdown")
    assert len(view.targets()) == 7
    view.page.click('#targets [data-format="pdf"]')
    view.submit()                                   # kode tugas 43 karakter harus terbungkus
    metrics = view.page.evaluate("""() => {
        const wheel = document.querySelector('.wheel').getBoundingClientRect();
        const hub = document.querySelector('#pick').getBoundingClientRect();
        const choices = [...document.querySelectorAll('#targets button')].map(b => b.getBoundingClientRect());
        const overlap = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
        return {
          scroll: document.documentElement.scrollWidth, inner: window.innerWidth,
          wheel: [wheel.left, wheel.right, wheel.width],
          inside: choices.every(c => c.left >= wheel.left - 1 && c.right <= wheel.right + 1
                                   && c.top >= wheel.top - 1 && c.bottom <= wheel.bottom + 1),
          hubFree: choices.every(c => !overlap(c, hub)),
          apart: choices.every((c, i) => choices.every((d, j) => i === j || !overlap(c, d))),
          minHeight: Math.min(...choices.map(c => c.height)),
        };
    }""")
    assert metrics["scroll"] <= metrics["inner"], metrics
    assert metrics["wheel"][2] == 280 and metrics["wheel"][0] >= 0 and metrics["wheel"][1] <= width
    assert metrics["inside"] and metrics["hubFree"] and metrics["apart"], metrics
    assert metrics["minHeight"] >= 44
    view.shot(f"06_responsif_{width}")


# ------------------------------------------------------------------ alur tahap 2

def test_close_browser_then_download_with_code_in_new_context(ui):
    first = ui()
    first.choose("data.csv", CSV, "text/csv")
    first.page.click('#targets [data-format="json"]')
    kode = first.submit()
    assert len(kode) == 43
    assert "boleh ditutup" in first.page.text_content("#jobBox")
    first.close()                                    # browser ditutup setelah unggahan selesai

    second = ui()
    assert second.page.locator("#jobList li").count() == 0   # perangkat/sesi lain: daftar kosong
    second.page.fill("#codeInput", f"  {kode}  ")
    second.page.click("#codeCheck")
    button = second.page.locator("#jobList li button.get")
    button.wait_for(state="visible", timeout=180_000)
    with second.page.expect_download() as info:
        button.click()
    assert info.value.suggested_filename == "hasil.json"
    assert json.loads(Path(info.value.path()).read_bytes()) == CSV_RECORDS
    second.shot("07_unduh_dengan_kode")


def test_corrupted_local_storage_does_not_break_page(ui):
    from konversiku import jobs

    view = ui()
    view.allowed_network_errors = 1                  # 404 untuk kode yang tidak dikenal
    view.page.evaluate("key => localStorage.setItem(key, '{rusak')", STORE)
    view.open()
    assert view.page.locator(".wheel").is_visible()
    assert view.page.locator("#jobList li").count() == 0
    unknown, _ = jobs.new_code()
    view.page.evaluate("([key, kode]) => localStorage.setItem(key, JSON.stringify(["
                       "{kode, nama: 'lama.md', source: 'md', target: 'pdf', dibuat: '2026-11-01'}, 42]))",
                       [STORE, unknown])
    view.open()
    item = view.page.locator("#jobList li")
    item.first.wait_for(timeout=30_000)
    assert item.count() == 1
    view.page.wait_for_function("() => document.querySelector('#jobList li').textContent.includes('kedaluwarsa')")
    item.locator("button.remove").click()
    assert view.page.locator("#jobList li").count() == 0
    assert view.page.evaluate("key => localStorage.getItem(key)", STORE) == "[]"


def test_double_click_creates_one_job(ui):
    view = ui()
    posts = []
    view.page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and _is_submit(r.url) else None)
    view.choose("data.csv", CSV, "text/csv")
    view.page.click('#targets [data-format="json"]')
    view.page.dblclick("#convert")
    view.page.wait_for_selector("#jobBox:not([hidden])", timeout=60_000)
    view.page.wait_for_timeout(500)
    assert len(posts) == 1
    assert view.page.locator("#jobList li").count() == 1


def test_expired_job_shows_clear_message(ui):
    view = ui()
    expired = {"status": "expired", "source": "csv", "target": "json", "dibuat": None, "selesai": None,
               "kedaluwarsa": None, "pesan": "Hasil sudah kedaluwarsa dan dihapus.", "catatan": [],
               "posisi_antrean": None}
    view.page.route("**/api/jobs/status", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(expired)))
    view.choose("data.csv", CSV, "text/csv")
    view.page.click('#targets [data-format="json"]')
    view.submit()
    view.wait_status("kedaluwarsa", timeout=30_000)
    assert view.page.is_hidden("#download")
