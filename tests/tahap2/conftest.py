"""Fixture tahap 2: pengaturan uji, schema PostgreSQL sementara, koneksi.

Tes yang butuh PostgreSQL/Redis ditandai ``@pytest.mark.layanan``. Bila layanan tidak
terjangkau (mis. di laptop) tes dilewati dengan alasan jelas; dengan
``KONVERSIKU_WAJIB_LAYANAN=1`` (dipakai di VPS) kondisi itu menjadi kegagalan.
"""
from __future__ import annotations

import dataclasses
import os
import uuid

import pytest

_PG_DOWN: list[Exception] = []


def _unavailable(what: str, exc: Exception):
    message = f"BELUM DIUJI: {what} tidak tersedia ({type(exc).__name__})"
    if os.environ.get("KONVERSIKU_WAJIB_LAYANAN") == "1":
        pytest.fail(message)
    pytest.skip(message)


def _redis_db1(url: str) -> str:
    """Tes memakai database Redis 1 agar terpisah dari dev (database 0)."""
    base, _, last = url.rpartition("/")
    return f"{base}/1" if last.isdigit() else f"{url.rstrip('/')}/1"


@pytest.fixture()
def settings(tmp_path):
    from konversiku import settings as settings_mod

    base = settings_mod.load_settings()
    return dataclasses.replace(
        base,
        data_dir=tmp_path / "data",
        db_schema=f"uji_{uuid.uuid4().hex[:8]}",
        redis_url=_redis_db1(base.redis_url),
    )


@pytest.fixture()
def storage(settings):
    from konversiku.storage import Storage

    store = Storage(settings.data_dir)
    store.ensure()
    return store


@pytest.fixture()
def connect_again(settings):
    """Fabrik koneksi tambahan ke schema uji yang sama (untuk tes konkurensi)."""
    from konversiku import db

    opened = []

    def factory():
        connection = db.connect(settings)
        opened.append(connection)
        return connection

    yield factory
    for connection in opened:
        connection.close()


@pytest.fixture()
def conn(settings):
    import psycopg
    from psycopg import sql

    from konversiku import db

    if _PG_DOWN:
        _unavailable("PostgreSQL", _PG_DOWN[0])
    try:
        admin = psycopg.connect(settings.database_url, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:
        _PG_DOWN.append(exc)  # cukup sekali per sesi tes; jangan menunggu timeout tiap tes
        _unavailable("PostgreSQL", exc)
    schema = sql.Identifier(settings.db_schema)
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
    connection = db.connect(settings)
    try:
        db.install_schema(connection)
        yield connection
    finally:
        connection.close()
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))
        admin.close()
