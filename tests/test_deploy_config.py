"""Konfigurasi produksi (deploy/systemd) harus terpisah dari stack dev dan tes.

Redis dipakai bersama satu server: dev memakai database 0, tes database 1. Bila produksi
juga memakai database 0, pekerja dev mengambil id tugas produksi (lalu "skipped" karena
barisnya ada di database lain) dan tugas produksi tertunda sampai pemulihan.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy" / "systemd" / "konversiku-pekerja.service"
ENV = ROOT / "deploy" / "systemd" / "konversiku.env.contoh"
DEV = ROOT / "deploy" / "dev" / "dev.sh"
NGINX = ROOT / "deploy" / "nginx" / "konversiku.conf"
PROXY = ROOT / "deploy" / "nginx" / "konversiku-proxy.conf"


def _env() -> dict[str, str]:
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _db_index(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[1]


def test_production_redis_db_differs_from_dev_and_tests():
    from konversiku.settings import load_settings

    dev = load_settings({}).redis_url
    production = _env()["KONVERSIKU_REDIS_URL"]
    assert _db_index(dev) == "0"
    assert _db_index(production) not in ("0", "1"), production


def test_production_worker_name_differs_from_dev():
    production = re.search(r"-n (\S+)", UNIT.read_text(encoding="utf-8")).group(1)
    dev = re.search(r"-n (\S+)", DEV.read_text(encoding="utf-8")).group(1)
    assert production.split("@")[0] != dev.split("@")[0], (production, dev)


def test_production_database_and_data_dir_differ_from_dev():
    from konversiku.settings import load_settings

    dev = load_settings({})
    env = _env()
    assert env["KONVERSIKU_DATABASE_URL"] != dev.database_url
    assert Path(env["KONVERSIKU_DATA_DIR"]) != dev.data_dir


def test_nginx_body_limit_follows_largest_upload():
    """Nginx menerima file sebesar batas terbesar (+1 MiB agar 413 yang jelas datang dari aplikasi)."""
    from konversiku import limits

    conf = NGINX.read_text(encoding="utf-8")
    largest = limits.MAX_UPLOAD_BYTES // limits.MiB
    assert re.search(r"^\s*client_max_body_size (\d+)m;", conf, re.M).group(1) == str(largest + 1)
    assert re.search(r"^\s*client_body_timeout 300s;", conf, re.M)
    assert f"melebihi batas {limits.size_label(limits.MAX_UPLOAD_BYTES)}" in conf
    proxy = PROXY.read_text(encoding="utf-8")
    assert re.search(r"^proxy_read_timeout 300s;", proxy, re.M)
    assert re.search(r"^proxy_send_timeout 300s;", proxy, re.M)


def test_worker_unit_limits_memory_and_waits_for_running_task():
    from konversiku import limits

    unit = UNIT.read_text(encoding="utf-8")
    assert re.search(r"^MemoryMax=5G$", unit, re.M)
    stop = int(re.search(r"^TimeoutStopSec=(\d+)$", unit, re.M).group(1))
    assert stop >= limits.TASK_HARD_TIME_LIMIT
