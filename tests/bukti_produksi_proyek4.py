"""Bukti proyek 4 di produksi lewat Nginx: file tepat sebesar batas selesai, di atas batas ditolak.

Pemakaian (dari hasil/, laptop di jaringan kampus atau VPN):
    python tests/bukti_produksi_proyek4.py http://10.33.109.96 > laporan_tes/proyek4_bukti_produksi.txt

Kode tugas tidak dicetak.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import ukur_kapasitas_proyek4 as ukur  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://10.33.109.96").rstrip("/")
MiB = 1024 * 1024


def post(source: str, target: str, data: bytes) -> tuple[int, dict, float]:
    request = urllib.request.Request(f"{BASE}/api/jobs?source={source}&target={target}", data=data, method="POST",
                                     headers={"Content-Type": "application/octet-stream"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return response.status, json.load(response), time.monotonic() - start
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}"), time.monotonic() - start


def get(path: str, kode: str) -> tuple[int, bytes]:
    request = urllib.request.Request(BASE + path, headers={"X-Kode-Tugas": kode})
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def exact_csv(size: int) -> tuple[bytes, int]:
    data = ukur.csv_(size - 1024)
    prefix = "9999999,Penutup,Yogyakarta,"
    pad = size - len(data) - len(prefix) - 2
    data += (prefix + "x" * pad + "\r\n").encode()
    assert len(data) == size
    return data, data.count(b"\r\n") - 1


def main() -> None:
    print(f"Server: {BASE} | waktu: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    with urllib.request.urlopen(BASE + "/api/formats", timeout=30) as response:
        per = json.load(response)["limits"]["max_upload_bytes_per_source"]
    print("Batas per sumber (MiB):", {k: v / MiB for k, v in per.items()})

    limit = per["csv"]
    data, rows = exact_csv(limit)
    status, body, seconds = post("csv", "json", data)
    print(f"1. CSV tepat di batas: {len(data)} byte, {rows} baris -> HTTP {status} ({seconds:.1f} s unggah)")
    assert status == 202, body
    kode, start, info = body["kode"], time.monotonic(), {}
    while time.monotonic() - start < 900:
        code, raw = get("/api/jobs/status", kode)
        info = json.loads(raw)
        if info["status"] in ("success", "failed"):
            break
        time.sleep(3)
    print(f"   status akhir: {info['status']} setelah {time.monotonic() - start:.1f} s menunggu")
    assert info["status"] == "success", info
    code, result = get("/api/jobs/hasil", kode)
    records = json.loads(result)
    last = records[-1]
    ok = len(records) == rows and last["kode"] == "9999999" and len(last["catatan"]) == len(data.split(b"\r\n")[-2].split(b",")[-1])
    print(f"   unduh: HTTP {code}, {len(result)} byte JSON, {len(records)} rekaman, isi benar: {ok}")
    assert code == 200 and ok

    status, body, _ = post("csv", "json", data + b"1")
    print(f"2. CSV 1 byte di atas batas -> HTTP {status}: {body.get('detail')}")
    assert status == 413 and "CSV" in body["detail"]

    status, body, _ = post("csv", "json", data + b"x" * MiB)
    print(f"3. CSV 1 MiB di atas batas -> HTTP {status}: {body.get('detail')}")
    assert status == 413

    status, body, _ = post("csv", "json", data + b"x" * (2 * MiB))
    print(f"4. CSV 2 MiB di atas batas (lewat batas Nginx 26 MiB) -> HTTP {status}: {body.get('detail')}")
    assert status == 413

    status, body, _ = post("yml", "json", b"a" * (per["yaml"] + 1))
    print(f"5. Alias .yml 1 byte di atas batas YAML -> HTTP {status}: {body.get('detail')}")
    assert status == 413 and "YAML" in body["detail"]

    status, body, _ = post("txt", "md", b"a" * (per["txt"] + 1))
    print(f"6. TXT 1 byte di atas batas TXT -> HTTP {status}: {body.get('detail')}")
    assert status == 413 and "TXT" in body["detail"]
    print("selesai: semua bukti lolos")


if __name__ == "__main__":
    main()
