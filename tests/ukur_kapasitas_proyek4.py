"""Ukur kapasitas proyek 4: waktu dan puncak memori konversi besar di mesin yang menjalankan.

Pemakaian (dari hasil/):
    python tests/ukur_kapasitas_proyek4.py > laporan_tes/kapasitas_proyek4.txt
    python tests/ukur_kapasitas_proyek4.py --cepat      # uji singkat 1 MiB

Setiap kasus dijalankan di subproses Python baru dengan batas ukur longgar, memanggil
mesin konversi langsung (``registry.get(s, t).run``). Subproses melaporkan waktu dan puncak
memori: max(ru_maxrss diri sendiri, ru_maxrss anak seperti Pandoc dan LibreOffice). Ukuran
berikutnya untuk satu pasangan dilewati bila ukuran sebelumnya melewati batas aturan.
Aturan batas proyek 4: waktu <= 300 detik dan puncak memori <= 1500 MiB per tugas.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

MiB = 1024 * 1024
BATAS_DETIK = 300
BATAS_MB = 1500
LONGGAR = {
    "PANDOC_TIMEOUT": 900, "LIBREOFFICE_TIMEOUT": 900, "PDF_TIMEOUT": 900, "PANDOC_HEAP": "1536M",
    "MAX_OUTPUT_BYTES": 1 << 30, "MAX_TEXT_CHARS": 500_000_000, "MAX_RECORDS": 10_000_000,
    "MAX_CELLS": 200_000_000, "MAX_ZIP_EXPANDED_BYTES": 1 << 30, "MAX_NODES": 100_000_000,
    "MAX_PDF_PAGES": 5000, "PDF_MEMORY_BYTES": 3 << 30,
}

ANAK = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
try:
    import resource
except ImportError:
    resource = None
from konversiku import limits, registry
for key, value in json.loads(sys.argv[2]).items():
    setattr(limits, key, value)
source, target, path = sys.argv[3], sys.argv[4], sys.argv[5]
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

PARAGRAF = ("Komputasi awan memindahkan beban konversi ke server sehingga perangkat pengguna tidak "
            "perlu bekerja keras. Paragraf ini sengaja panjang agar menyerupai laporan tugas dengan "
            "kalimat biasa, sedikit penekanan, dan huruf Ñandú. ") * 3


def _fill(block: str, size: int) -> bytes:
    data = (block * (size // len(block.encode()) + 1)).encode()
    return data[:size].decode("utf-8", "ignore").encode()


def md_prosa(size): return _fill("## Bab\n\n" + (PARAGRAF + "\n\n") * 20, size)
def md_padat(size): return _fill("## Bagian\n\nParagraf berisi teks contoh dengan **tebal** dan Ñandú.\n\n- satu\n- dua\n\n", size)
def txt(size): return _fill((PARAGRAF + "\n\n") * 20, size)
def html(size):
    body = _fill("<h2>Bab</h2>\n" + ("<p>" + PARAGRAF + "</p>\n") * 20, size - 60)
    return b"<!doctype html><html><body>\n" + body + b"\n</body></html>"
def rst(size): return _fill("Bab\n===\n\n" + (PARAGRAF + "\n\n") * 20, size)
def tex(size):
    body = _fill("\\section{Bab}\n\n" + (PARAGRAF.replace("Ñ", "N") + "\n\n") * 20, size - 60)
    return b"\\documentclass{article}\n\\begin{document}\n" + body + b"\n\\end{document}\n"
def rtf(size):
    para = PARAGRAF.replace("Ñandú", "\\u209?and\\u250?") + "\\par\n"
    return b"{\\rtf1\\ansi\\deff0 " + _fill("{\\b Bab}\\par\n" + para * 20, size - 20) + b"}"
def docx(size):
    from conftest import make_docx
    text = md_prosa(size).decode("utf-8", "ignore")
    return make_docx(heading="Laporan", paragraphs=[p for p in text.split("\n\n") if p.strip() and not p.startswith("#")])
def pdf(size):
    from conftest import make_text_pdf
    words = (PARAGRAF.replace("Ñandú", "Nandu")).split()
    lines, line = [], ""
    while sum(len(x) + 1 for x in lines) < size:
        for word in words:
            if len(line) + len(word) > 90:
                lines.append(line)
                line = ""
            line += word + " "
    pages = [lines[i:i + 45] for i in range(0, len(lines), 45)]
    return make_text_pdf(pages)
def csv_(size, sep=","):
    head = sep.join(["kode", "nama", "kota", "catatan"]) + "\r\n"
    row = sep.join(["{:07d}", "Budi Santoso", "Yogyakarta", "baris contoh berisi teks"]) + "\r\n"
    rows, total, i = [head], len(head), 0
    while total < size:
        line = row.format(i)
        rows.append(line)
        total += len(line)
        i += 1
    return "".join(rows).encode()
def tsv(size): return csv_(size, "\t")
def json_(size):
    item = '{{"kode": "{:07d}", "profil": {{"nama": "Budi", "kota": "Yogyakarta", "tag": ["a", "b"]}}}}'
    parts, total, i = [], 2, 0
    while total < size:
        piece = item.format(i)
        parts.append(piece)
        total += len(piece) + 2
        i += 1
    return ("[" + ", ".join(parts) + "]").encode()
def yaml_(size):
    block = "- kode: '{:07d}'\n  profil:\n    nama: Budi\n    kota: Yogyakarta\n    tag: [a, b]\n"
    parts, total, i = [], 0, 0
    while total < size:
        piece = block.format(i)
        parts.append(piece)
        total += len(piece)
        i += 1
    return "".join(parts).encode()
def xml(size):
    block = "<item><kode>{:07d}</kode><nama>Budi</nama><kota>Yogyakarta</kota></item>\n"
    parts, total, i = ["<data>\n"], 8, 0
    while total < size:
        piece = block.format(i)
        parts.append(piece)
        total += len(piece)
        i += 1
    parts.append("</data>\n")
    return "".join(parts).encode()


DOK = [3, 5, 10, 20]
DATA = [10, 25, 50]
KASUS = [  # (sumber, target, nama isi, pembuat, ukuran MiB)
    ("md", "html", "prosa", md_prosa, DOK), ("md", "docx", "prosa", md_prosa, DOK),
    ("md", "pdf", "prosa", md_prosa, DOK), ("md", "docx", "padat", md_padat, DOK),
    ("md", "html", "padat", md_padat, DOK), ("txt", "docx", "prosa", txt, DOK),
    ("txt", "pdf", "prosa", txt, DOK), ("txt", "md", "prosa", txt, DOK),
    ("html", "md", "prosa", html, DOK), ("html", "docx", "prosa", html, DOK), ("html", "pdf", "prosa", html, DOK),
    ("rst", "html", "prosa", rst, DOK), ("rst", "pdf", "prosa", rst, DOK),
    ("tex", "html", "prosa", tex, DOK), ("tex", "pdf", "prosa", tex, DOK),
    ("rtf", "md", "prosa", rtf, DOK), ("rtf", "pdf", "prosa", rtf, DOK),
    ("docx", "md", "prosa (ukuran teks)", docx, DOK), ("docx", "pdf", "prosa (ukuran teks)", docx, DOK),
    ("pdf", "md", "teks", pdf, DOK), ("pdf", "docx", "teks", pdf, DOK),
    ("csv", "json", "tabel", csv_, DATA), ("csv", "xml", "tabel", csv_, DATA), ("tsv", "json", "tabel", tsv, DATA),
    ("json", "csv", "bersarang", json_, DATA), ("json", "yaml", "bersarang", json_, DATA),
    ("json", "xml", "bersarang", json_, DATA), ("yaml", "json", "bersarang", yaml_, DATA),
    ("xml", "json", "elemen", xml, DATA),
]


def run_case(source, target, path) -> dict:
    try:
        done = subprocess.run([sys.executable, "-c", ANAK, str(ROOT), json.dumps(LONGGAR), source, target, str(path)],
                              capture_output=True, text=True, timeout=1200)
        line = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
        return json.loads(line) if line.startswith("{") else {"status": "gagal", "detik": -1, "puncak_mb": -1,
                                                              "hasil_byte": 0, "galat": done.stderr[-160:]}
    except subprocess.TimeoutExpired:
        return {"status": "gagal", "detik": 1200, "puncak_mb": -1, "hasil_byte": 0, "galat": "melewati 1200 detik"}


def header() -> None:
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
    print(f"Batas ukur longgar: {json.dumps(LONGGAR)}")
    print(f"Aturan batas: waktu <= {BATAS_DETIK} detik dan puncak memori <= {BATAS_MB} MiB")
    print()


def main() -> None:
    quick = "--cepat" in sys.argv
    header()
    workdir = Path(tempfile.mkdtemp(prefix="kk-ukur-"))
    for source, target, kind, maker, sizes in KASUS:
        if quick and (source, target) not in {("md", "html"), ("csv", "json"), ("docx", "md")}:
            continue
        for size_mib in ([1] if quick else sizes):
            data = maker(int(size_mib * MiB))
            path = workdir / f"input.{source}"
            path.write_bytes(data)
            result = run_case(source, target, path)
            lolos = (result["status"] == "ok" and 0 <= result["detik"] <= BATAS_DETIK
                     and result["puncak_mb"] <= BATAS_MB)
            print(f"{source}->{target} [{kind}] target {size_mib} MiB: file={len(data)} byte "
                  f"status={result['status']} waktu={result['detik']} s puncak={result['puncak_mb']} MiB "
                  f"hasil={result['hasil_byte']} byte {'LOLOS' if lolos else 'TIDAK LOLOS'} {result['galat']}".rstrip(),
                  flush=True)
            if not lolos:
                break
    print(f"\nWaktu selesai: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")


if __name__ == "__main__":
    main()
