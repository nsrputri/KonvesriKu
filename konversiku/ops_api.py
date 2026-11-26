"""Pemantauan: ``/healthz`` (cek komponen) dan ``/metrics`` (format Prometheus).

Keduanya hanya untuk jaringan lokal dan Prometheus. Nginx (proyek 2) tidak boleh
meneruskan ``/metrics`` ke publik. Metrik tidak memuat id tugas, kode, atau hash.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response

from . import jobs
from .jobs_api import get_conn, get_storage
from .settings import broker_url, load_settings
from .storage import Storage
from .web import NO_STORE

router = APIRouter()
log = logging.getLogger("konversiku.ops")
QUEUE = "konversiku"  # sama dengan celery_app.QUEUE


def get_db_check():
    def check() -> None:
        from . import db

        conn = db.connect(load_settings())
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()

    return check


def get_redis():
    import redis

    return redis.Redis.from_url(broker_url(load_settings()), socket_timeout=3, socket_connect_timeout=3)


def _check(name: str, probe) -> str:
    try:
        probe()
    except Exception as exc:  # noqa: BLE001  (laporkan komponen yang gagal, bukan 500)
        log.warning("healthz: %s gagal (%s)", name, type(exc).__name__)
        return "gagal"
    return "ok"


def _storage_probe(storage: Storage) -> None:
    probe = storage.incoming / f".healthz-{secrets.token_hex(4)}"
    probe.write_bytes(b"ok")
    probe.unlink()


@router.get("/healthz")
def healthz(db_check=Depends(get_db_check), client=Depends(get_redis),
            storage: Storage = Depends(get_storage)):
    try:
        components = {
            "postgresql": _check("postgresql", db_check),
            "redis": _check("redis", client.ping),
            "penyimpanan": _check("penyimpanan", lambda: _storage_probe(storage)),
        }
    finally:
        getattr(client, "close", lambda: None)()
    healthy = all(value == "ok" for value in components.values())
    return JSONResponse({"status": "ok" if healthy else "gagal", "komponen": components},
                        status_code=200 if healthy else 503, headers=NO_STORE)


@router.get("/metrics")
def metrics(conn=Depends(get_conn), client=Depends(get_redis)):
    from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest

    registry = CollectorRegistry()
    tugas = Gauge("konversiku_tugas", "Jumlah tugas per status di PostgreSQL", ["status"],
                  registry=registry)
    for status, count in jobs.count_by_status(conn).items():
        tugas.labels(status=status).set(count)
    selesai = Counter("konversiku_tugas_selesai", "Total tugas berhasil dan gagal sepanjang waktu",
                      ["hasil"], registry=registry)
    for outcome, count in jobs.totals(conn).items():
        selesai.labels(hasil=outcome).inc(count)
    conn.commit()
    antrean = Gauge("konversiku_antrean_redis", "Panjang antrean Celery di Redis (NaN bila Redis mati)",
                    registry=registry)
    try:
        antrean.set(client.llen(QUEUE))
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics: Redis gagal (%s)", type(exc).__name__)
        antrean.set(float("nan"))
    finally:
        getattr(client, "close", lambda: None)()
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST, headers=NO_STORE)
