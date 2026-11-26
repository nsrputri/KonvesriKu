"""Uji proyek 4: dua tugas terberat dikirim bersamaan ke produksi lewat Nginx.

Pemakaian (dari hasil/, laptop di jaringan kampus atau VPN), sesudah pekerja di-restart supaya
MemoryPeak unit systemd mulai dari nol:
    python tests/uji_bersamaan_proyek4.py http://10.33.109.96 md:pdf txt:md

Setiap argumen ``sumber:target`` memakai file terberat untuk sumber itu sebesar batasnya (bentuk
yang sama dengan verifikasi). Memori total pekerja (cgroup unit, termasuk Pandoc dan LibreOffice)
dibaca terpisah di VPS: ``systemctl show konversiku-pekerja -p MemoryPeak``. Kode tugas tidak dicetak.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import bukti_produksi_proyek4 as bukti  # noqa: E402
import ukur_kapasitas_proyek4 as ukur  # noqa: E402
import verifikasi_batas_proyek4 as verif  # noqa: E402

MAKERS = {"txt": verif.txt_pendek, "md": ukur.md_prosa, "html": ukur.html, "rst": ukur.rst, "tex": ukur.tex,
          "rtf": ukur.rtf, "csv": lambda size: verif._cut(ukur.csv_, size),
          "json": lambda size: verif._cut(ukur.json_, size), "yaml": lambda size: verif._cut(ukur.yaml_, size),
          "xml": lambda size: verif._cut(ukur.xml, size)}


def run(source: str, target: str, limit: int, out: dict) -> None:
    data = MAKERS[source](limit)
    start = time.monotonic()
    status, body, upload = bukti.post(source, target, data)
    if status != 202:
        out[(source, target)] = f"submit HTTP {status}: {body.get('detail')}"
        return
    info = {}
    while time.monotonic() - start < 1500:
        _, raw = bukti.get("/api/jobs/status", body["kode"])
        info = json.loads(raw)
        if info["status"] in ("success", "failed"):
            break
        time.sleep(2)
    code, result = bukti.get("/api/jobs/hasil", body["kode"])
    out[(source, target)] = (f"{len(data)} byte, unggah {upload:.1f} s, status {info.get('status')} setelah "
                             f"{time.monotonic() - start:.1f} s total, unduh HTTP {code} {len(result)} byte"
                             + (f", pesan: {info.get('pesan')}" if info.get("pesan") else ""))


def main() -> None:
    bukti.BASE = sys.argv[1].rstrip("/")
    pairs = [arg.split(":") for arg in sys.argv[2:]]
    with bukti.urllib.request.urlopen(bukti.BASE + "/api/formats", timeout=30) as response:
        per = json.load(response)["limits"]["max_upload_bytes_per_source"]
    print(f"Server: {bukti.BASE} | mulai: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    out: dict = {}
    threads = [threading.Thread(target=run, args=(s, t, per[s], out)) for s, t in pairs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for (source, target), line in out.items():
        print(f"{source}->{target} sebesar batas {per[source] / bukti.MiB:g} MiB: {line}")
    print(f"selesai: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")


if __name__ == "__main__":
    main()
