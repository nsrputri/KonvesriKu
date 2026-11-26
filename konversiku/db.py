"""Koneksi PostgreSQL. psycopg diimpor saat dipakai agar aplikasi tetap bisa diimpor tanpa
PostgreSQL menyala (tes tahap 1 di laptop)."""
from __future__ import annotations

from pathlib import Path

from .settings import Settings

SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def connect(settings: Settings):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    conn = psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=5)
    conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(settings.db_schema)))
    conn.commit()
    return conn


def install_schema(conn) -> None:
    conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()
