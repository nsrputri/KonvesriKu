"""Pengukuran kapasitas sederhana tahap 1 (bukan bagian pytest; jalankan manual).

    python tests/ukur_kapasitas.py > laporan_tes/kapasitas.txt

Mengirim input mendekati batas upload 2 MiB ke beberapa pasangan berat lewat API
(TestClient, proses yang sama) dan mencatat status, waktu, ukuran hasil, serta ukuran
profil LibreOffice. Ini bukan uji beban dan bukan bukti target 1 GB.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["KONVERSIKU_SYNC_ENDPOINT"] = "1"  # endpoint sinkron tahap 1 khusus pengukuran

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
from konversiku import limits, tools  # noqa: E402

LIMIT = limits.MAX_UPLOAD_BYTES


def _fill(block: str, size: int) -> bytes:
    data = (block * (size // len(block.encode()) + 1)).encode()
    return data[:size]


def markdown_dense() -> bytes:
    """Kasus ekstrem: ~20 ribu judul dan ~40 ribu butir daftar dalam 2 MiB."""
    return _fill("## Bagian\n\nParagraf berisi teks contoh dengan **tebal** dan Ñandú.\n\n- satu\n- dua\n\n",
                 LIMIT - 1024)


def markdown_prose(size: int = LIMIT - 1024) -> bytes:
    """Dokumen prosa biasa: satu judul per 20 paragraf panjang."""
    paragraph = ("Komputasi awan memindahkan beban konversi ke server sehingga perangkat pengguna tidak "
                 "perlu bekerja keras. Paragraf ini sengaja panjang agar menyerupai laporan tugas dengan "
                 "kalimat biasa, sedikit **penekanan**, dan huruf Ñandú. ") * 3
    return _fill("## Bab\n\n" + (paragraph + "\n\n") * 20, size)


def docx_payload() -> bytes:
    from conftest import make_docx

    text = markdown_prose(LIMIT // 2).decode("utf-8", "ignore")
    return make_docx(heading="Laporan", paragraphs=[p for p in text.split("\n\n") if p.strip()])


def csv_payload() -> bytes:
    lines = ["kode,nama,kota,catatan"]
    row = '001,"Budi, S.T.",Yogyakarta,"baris satu"'
    size = len(lines[0]) + 2
    count = 0
    while size + len(row) + 2 < LIMIT - 1024 and count < limits.MAX_RECORDS:
        lines.append(row)
        size += len(row) + 2
        count += 1
    return ("\r\n".join(lines) + "\r\n").encode()


def json_payload() -> bytes:
    records = [{"kode": f"{i:05d}", "profil": {"nama": "Budi", "tag": ["a", "b"]}} for i in range(10_000)]
    return json.dumps(records).encode()


def profile_size() -> int:
    path = Path(tempfile.gettempdir()) / f"konversiku-lo-profile-{os.getpid()}"
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


CASES = [
    ("md", "html", markdown_prose),
    ("md", "docx", markdown_prose),
    ("md", "pdf", markdown_prose),
    ("docx", "pdf", docx_payload),
    ("md", "html", markdown_dense),
    ("md", "docx", markdown_dense),
    ("csv", "json", csv_payload),
    ("json", "csv", json_payload),
    ("json", "xml", json_payload),
]


def main() -> None:
    found = tools.detect()
    print(f"Python {platform.python_version()} | {platform.platform()}")
    print(f"pandoc={found.pandoc_version} libreoffice={found.libreoffice_version}")
    print(f"batas upload={LIMIT} byte\n")
    with TestClient(app.app) as client:
        for source, target, make in CASES:
            payload = make()
            started = time.perf_counter()
            response = client.post("/api/convert", params={"source": source, "target": target}, content=payload)
            elapsed = time.perf_counter() - started
            detail = "" if response.status_code == 200 else response.json().get("detail", "")
            print(f"{source}->{target} [{make.__name__}]: input={len(payload)} byte status={response.status_code} "
                  f"waktu={elapsed:.1f} s hasil={len(response.content)} byte {detail}")
    print(f"\nukuran profil LibreOffice proses ini: {profile_size() / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
