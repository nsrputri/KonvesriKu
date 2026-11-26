"""Verifikasi batas proyek 4: semua pasangan dengan file sebesar batas dan batas pendamping final.

Pemakaian (dari hasil/):
    python tests/verifikasi_batas_proyek4.py > laporan_tes/kapasitas_proyek4_verifikasi.txt
    python tests/verifikasi_batas_proyek4.py --cepat     # uji singkat beberapa pasangan, file kecil
    python tests/verifikasi_batas_proyek4.py --sumber txt,docx   # hanya format sumber tertentu

Berbeda dari ukur_kapasitas_proyek4.py, subproses memakai batas di konversiku/limits.py apa adanya
(tanpa dilonggarkan), sehingga yang diuji adalah perilaku layanan sebenarnya: file sebesar batas
harus lolos semua batas lain dan selesai <= 300 detik dengan beban memori <= 1500 MiB. Bila sebuah
ukuran gagal, ukuran berikutnya di tangga diuji; batas akhir per format = ukuran terkecil yang
lolos di antara semua target dan bentuk isinya.

Beban memori = max(RSS proses terbesar dari getrusage, puncak jumlah RSS seluruh pohon proses).
Jumlah pohon diambil dari /proc tiap 0,1 detik (proses Python yang memegang AST ditambah Pandoc
atau LibreOffice yang sedang berjalan); halaman bersama terhitung ganda, jadi angkanya sedikit
melebihi pemakaian nyata. Bentuk "padat" menguji batas isi: boleh ditolak dengan pesan batas,
asal penolakan itu juga <= 300 detik dan <= 1500 MB.
"""
from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import ukur_kapasitas_proyek4 as ukur  # noqa: E402
from konversiku import limits, registry  # noqa: E402

MiB = limits.MiB
BATAS_DETIK = ukur.BATAS_DETIK
BATAS_MB = ukur.BATAS_MB

ANAK = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
try:
    import resource
except ImportError:
    resource = None
from konversiku import registry
source, target, path = sys.argv[2], sys.argv[3], sys.argv[4]
data = open(path, "rb").read()
status, out, err = "ok", 0, ""
start = time.monotonic()
try:
    out = len(registry.get(source, target).run(data).data)
except Exception as exc:
    status, err = "gagal", f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))[:160]}"
seconds = time.monotonic() - start
peak = -1
if resource:
    peak = max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
               resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) // 1024
print(json.dumps({"status": status, "detik": round(seconds, 1), "puncak_mb": peak, "hasil_byte": out, "galat": err}))
"""


# ------------------------------------------------------------------ bentuk isi

def txt_pendek(size: int) -> bytes:
    block = "".join(f"baris pendek nomor {i:06d} Ñ\n" for i in range(1000))
    return ukur._fill(block, size)


def _cut(maker, size: int) -> bytes:
    """Pembuat data ukur berhenti setelah melewati ukuran; beri ruang satu rekaman."""
    data = maker(size - 1024)
    assert len(data) <= size, (maker.__name__, len(data), size)
    return data


def docx_di_batas_xml() -> bytes:
    import zipfile

    from conftest import make_docx

    cap = limits.MAX_DOCX_XML_BYTES
    size = int(cap * 0.82)
    while True:
        text = ukur.md_prosa(size).decode("utf-8", "ignore")
        paragraphs = [p for p in text.split("\n\n") if p.strip() and not p.startswith("#")]
        chars = len("Laporan\n" + "\n".join(paragraphs))
        raw = make_docx(heading="Laporan", paragraphs=paragraphs)
        infos = zipfile.ZipFile(io.BytesIO(raw)).infolist()
        xml = sum(i.file_size for i in infos if i.filename.endswith((".xml", ".rels")))
        if xml <= cap and chars <= limits.MAX_TEXT_CHARS:
            return raw
        size = int(size * 0.98)


def _png_acak(width: int, height: int) -> bytes:
    rows = b"".join(b"\x00" + os.urandom(width * 3) for _ in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b"")


def docx_bergambar() -> bytes:
    """DOCX mendekati batas file: satu gambar acak besar (tidak bisa dikompresi) dan teks biasa."""
    from docx import Document
    from docx.shared import Inches

    document = Document()
    document.add_heading("Laporan bergambar", level=1)
    for paragraph in ukur.PARAGRAF.split(". ")[:40]:
        document.add_paragraph(paragraph + ".")
    document.add_picture(io.BytesIO(_png_acak(2400, 2700)), width=Inches(6))
    document.add_paragraph("Akhir laporan.")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def pdf_di_batas_halaman() -> bytes:
    from conftest import make_text_pdf

    words = ukur.PARAGRAF.replace("Ñandú", "Nandu").split()
    lines, line = [], ""
    while len(lines) < limits.MAX_PDF_PAGES * 45:
        for word in words:
            if len(line) + len(word) > 90:
                lines.append(line)
                line = ""
            line += word + " "
    lines = lines[:limits.MAX_PDF_PAGES * 45]
    return make_text_pdf([lines[i:i + 45] for i in range(0, len(lines), 45)])


def pdf_bergambar() -> bytes:
    """PDF mendekati batas file: halaman 1 berisi gambar RGB acak (FlateDecode), lalu 20 halaman teks."""
    width, height = 2400, 2700
    image = zlib.compress(os.urandom(width * height * 3), 1)
    text_pages = [[f"Halaman {p} baris {i} berisi teks laporan biasa." for i in range(40)] for p in range(2, 22)]
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids = [3 + 2 * i for i in range(1 + len(text_pages))]
    font_id = page_ids[-1] + 2
    image_id = font_id + 1
    objects.append(f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
                   f"/Count {len(page_ids)} >>".encode())
    contents = [b"q 500 0 0 560 56 160 cm /Im1 Do Q BT /F1 12 Tf 56 120 Td (Gambar hasil pindai) Tj ET"]
    for lines in text_pages:
        ops = " ".join(f"({line}) Tj T*" for line in lines)
        contents.append(f"BT /F1 12 Tf 72 720 Td 16 TL {ops} ET".encode())
    for index, stream in enumerate(contents):
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 "
                       f"{font_id} 0 R >> /XObject << /Im1 {image_id} 0 R >> >> /Contents "
                       f"{page_ids[index] + 1} 0 R >>".encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB "
                   f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(image)} >>\nstream\n".encode()
                   + image + b"\nendstream")
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


def _repeat(head: str, item: str, tail: str, size: int) -> bytes:
    count = (size - len(head) - len(tail)) // len(item)
    return (head + item * count + tail).encode()


def yaml_aliran(size): return _repeat("[", "1, ", "1]\n", size)
def yaml_blok(size): return _repeat("", "- 1\n", "", size)
def yaml_di_batas_node(size): return ("[" + ", ".join("1" * (limits.MAX_YAML_NODES - 1)) + "]\n").encode()
def json_objek_kecil(size): return _repeat("[", '{"a":1},', '{"a":1}]', size)
def json_angka(size): return _repeat("[", "1,", "1]", size)
def xml_elemen_kosong(size): return _repeat("<d>", "<i/>", "</d>", size)
def csv_sempit(size): return _repeat("a\r\n", "1\r\n", "", size)


def csv_lebar(size):
    header = ",".join(f"kolom_dengan_nama_yang_cukup_panjang_{i:03d}" for i in range(200))
    return (header + "\r\n" + ("," .join(["1"] * 200) + "\r\n") * 10_000).encode()


DENSE = {  # bentuk padat: boleh ditolak oleh batas isi
    "yaml": [("aliran [1, 1, ...]", yaml_aliran), ("blok - 1", yaml_blok),
             (f"tepat {limits.MAX_YAML_NODES - 1} node", yaml_di_batas_node)],
    "json": [("objek kecil", json_objek_kecil), ("angka", json_angka)],
    "xml": [("elemen kosong", xml_elemen_kosong)],
    "csv": [("satu kolom", csv_sempit), ("200 kolom nama panjang", csv_lebar)],
}

DOC_MAKERS = {"txt": ukur.txt, "md": ukur.md_prosa, "rtf": ukur.rtf, "html": ukur.html,
              "rst": ukur.rst, "tex": ukur.tex}
DATA_MAKERS = {"csv": ("tabel", ukur.csv_), "tsv": ("tabel", ukur.tsv), "json": ("bersarang", ukur.json_),
               "yaml": ("bersarang", ukur.yaml_), "xml": ("elemen", ukur.xml)}


def ladder(source: str) -> list[int]:
    top = limits.max_upload_bytes(source)
    steps = [top] + [s * MiB for s in (20, 15, 10, 8, 5, 4, 3) if s * MiB < top]
    return steps[:4]


def shapes(source: str):
    """(nama bentuk, fungsi pembuat ukuran -> bytes atau None, daftar ukuran)."""
    if source in DOC_MAKERS:
        found = [("prosa", DOC_MAKERS[source], ladder(source))]
        if source == "txt":
            found.append(("baris pendek", txt_pendek, ladder(source)))
        return found
    if source == "docx":
        return [("teks di batas XML", lambda size: docx_di_batas_xml(), [None]),
                ("bergambar", lambda size: docx_bergambar(), [None])]
    if source == "pdf":
        return [(f"teks {limits.MAX_PDF_PAGES} halaman", lambda size: pdf_di_batas_halaman(), [None]),
                ("bergambar", lambda size: pdf_bergambar(), [None])]
    kind, maker = DATA_MAKERS[source]
    return [(kind, lambda size, maker=maker: _cut(maker, size), ladder(source))]


def _children(pid: int) -> list[int]:
    found: list[int] = []
    try:
        for tid in os.listdir(f"/proc/{pid}/task"):
            with open(f"/proc/{pid}/task/{tid}/children") as handle:
                found.extend(int(x) for x in handle.read().split())
    except OSError:
        pass
    return found


def tree_rss_mb(root: int) -> int:
    """Jumlah RSS proses root dan semua turunannya saat ini (MiB); -1 bila /proc tidak ada."""
    if not os.path.isdir("/proc"):
        return -1
    page = os.sysconf("SC_PAGE_SIZE")
    total, stack, seen = 0, [root], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            with open(f"/proc/{pid}/statm") as handle:
                total += int(handle.read().split()[1]) * page
        except (OSError, ValueError, IndexError):
            continue
        stack.extend(_children(pid))
    return total // MiB


def run_case(source: str, target: str, path: Path) -> dict:
    proc = subprocess.Popen([sys.executable, "-c", ANAK, str(ROOT), source, target, str(path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak = {"pohon": -1}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak["pohon"] = max(peak["pohon"], tree_rss_mb(proc.pid))
            stop.wait(0.1)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        out, err = proc.communicate(timeout=1200)
        line = out.strip().splitlines()[-1] if out.strip() else ""
        result = json.loads(line) if line.startswith("{") else {
            "status": "gagal", "detik": -1, "puncak_mb": -1, "hasil_byte": 0, "galat": err[-160:]}
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        result = {"status": "gagal", "detik": 1200, "puncak_mb": -1, "hasil_byte": 0, "galat": "melewati 1200 detik"}
    stop.set()
    sampler.join()
    result["pohon_mb"] = peak["pohon"]
    result["beban_mb"] = max(result["puncak_mb"], peak["pohon"])
    return result


def _line(source, target, name, label, length, result, verdict) -> None:
    print(f"{source}->{target} [{name}] {label}: file={length} byte status={result['status']} "
          f"waktu={result['detik']} s puncak={result['puncak_mb']} MiB pohon={result['pohon_mb']} MiB "
          f"hasil={result['hasil_byte']} byte {verdict} {result['galat']}".rstrip(), flush=True)


def header() -> None:
    import platform

    from konversiku import tools
    found = tools.detect()
    print(f"Python {platform.python_version()} | {platform.platform()} | CPU {os.cpu_count()} inti")
    try:
        mem = [l for l in open("/proc/meminfo") if l.startswith(("MemTotal", "MemAvailable"))]
        print("RAM:", " | ".join(l.split(":")[0] + " " + l.split(":")[1].strip() for l in mem))
    except OSError:
        pass
    print(f"pandoc={found.pandoc_version} libreoffice={found.libreoffice_version}")
    print(f"Waktu mulai: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    print(f"Aturan: waktu <= {BATAS_DETIK} detik dan beban memori <= {BATAS_MB} MiB, batas limits.py apa adanya")


def main() -> None:
    quick = "--cepat" in sys.argv
    only = None
    if "--sumber" in sys.argv:
        only = set(sys.argv[sys.argv.index("--sumber") + 1].split(","))
    header()
    print("Batas yang diuji: per sumber " + json.dumps(
        {s: limits.max_upload_bytes(s) / MiB for s in registry.SOURCE_ORDER}) + " MiB")
    print(f"Pendamping: DOCX XML {limits.MAX_DOCX_XML_BYTES / MiB:g} MiB, PDF {limits.MAX_PDF_PAGES} halaman, "
          f"teks {limits.MAX_TEXT_CHARS} karakter, baris {limits.MAX_RECORDS}, sel {limits.MAX_CELLS}, "
          f"node {limits.MAX_NODES}, node YAML saat dibaca {limits.MAX_YAML_NODES}, hasil "
          f"{limits.MAX_OUTPUT_BYTES / MiB:g} MiB, heap Pandoc {limits.PANDOC_HEAP}, timeout Pandoc "
          f"{limits.PANDOC_TIMEOUT} s, LibreOffice {limits.LIBREOFFICE_TIMEOUT} s")
    print()
    workdir = Path(tempfile.mkdtemp(prefix="kk-verif-"))
    cache: dict = {}
    verdict: dict[str, list] = {}
    for source in registry.SOURCE_ORDER:
        if only and source not in only:
            continue
        targets = [p.target for p in registry.PAIRS if p.source == source]
        if quick:
            targets = targets[:1]
        for name, maker, sizes in shapes(source):
            for target in targets:
                passed = None
                for size in sizes:
                    key = (source, name, size)
                    if key not in cache:
                        data = maker(MiB // 4 if quick and size else size)
                        path = workdir / f"{len(cache)}.{source}"
                        path.write_bytes(data)
                        cache[key] = (path, len(data))
                    path, length = cache[key]
                    allowed = length <= limits.max_upload_bytes(source)
                    result = run_case(source, target, path) if allowed else {
                        "status": "gagal", "detik": 0, "puncak_mb": 0, "hasil_byte": 0, "galat": "di atas batas unggah"}
                    lolos = (result["status"] == "ok" and 0 <= result["detik"] <= BATAS_DETIK
                             and 0 <= result["beban_mb"] <= BATAS_MB)
                    label = f"{size / MiB:g} MiB" if size else "batas isi"
                    _line(source, target, name, label, length, result, "LOLOS" if lolos else "TIDAK LOLOS")
                    if lolos:
                        passed = (size, length, result)
                        break
                verdict.setdefault(source, []).append((target, name, passed))
        for name, maker in DENSE.get(source, []):
            data = maker(MiB // 4 if quick else limits.max_upload_bytes(source))
            assert len(data) <= limits.max_upload_bytes(source), (source, name, len(data))
            path = workdir / f"padat-{len(cache)}.{source}"
            path.write_bytes(data)
            cache[(source, name, "padat")] = (path, len(data))
            for target in targets:
                result = run_case(source, target, path)
                # Ditolak dengan pesan jelas (batas isi, atau bentuk yang tidak cocok untuk target
                # seperti daftar angka ke CSV) boleh, asal cepat dan hemat memori.
                rejected = result["status"] == "gagal" and result["galat"].startswith("ConversionError")
                fine = 0 <= result["detik"] <= BATAS_DETIK and 0 <= result["beban_mb"] <= BATAS_MB
                lolos = fine and (result["status"] == "ok" or rejected)
                tag = ("LOLOS" if result["status"] == "ok" else "DITOLAK DENGAN PESAN (LOLOS)") if lolos else "TIDAK LOLOS"
                _line(source, target, f"padat: {name}", f"{len(data) / MiB:.2f} MiB", len(data), result, tag)
                verdict.setdefault(source, []).append(
                    (target, f"padat: {name}", (None, len(data), result) if lolos else None))
    print(f"\nWaktu selesai: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    print("\n== Ringkasan per format sumber ==")
    for source, rows in verdict.items():
        failed = [f"{t} [{n}]" for t, n, p in rows if p is None]
        sizes = [p[0] for _, _, p in rows if p is not None and p[0]]
        slowest = max((p[2]["detik"] for _, _, p in rows if p), default=0)
        heaviest = max((p[2]["beban_mb"] for _, _, p in rows if p), default=0)
        if failed:
            line = "TIDAK LOLOS: " + ", ".join(failed)
        elif sizes:
            line = f"lolos sampai {min(sizes) / MiB:g} MiB"
        else:
            line = f"lolos (batas file {limits.max_upload_bytes(source) / MiB:g} MiB dan batas isi)"
        print(f"{source:5} batas {limits.max_upload_bytes(source) / MiB:g} MiB: {line}; "
              f"waktu terlama {slowest} s, beban memori terbesar {heaviest} MiB")


if __name__ == "__main__":
    main()
