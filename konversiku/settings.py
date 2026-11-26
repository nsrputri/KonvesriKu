"""Pengaturan tahap 2 dari variabel lingkungan, dengan nilai bawaan untuk VPS dev.

Password Redis hanya dibaca dari file (``KONVERSIKU_REDIS_PASSWORD_FILE``) dan hanya
dipakai untuk menyusun URL broker; nilainya tidak pernah dicetak atau di-log.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

DEV_ROOT = Path.home() / "konversiku-dev"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    db_schema: str
    redis_url: str
    redis_password_file: Path | None

    def __repr__(self) -> str:  # tidak ada rahasia di sini, tetapi tetap ringkas
        return (f"Settings(data_dir={str(self.data_dir)!r}, db_schema={self.db_schema!r}, "
                f"redis_url={self.redis_url!r})")


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    password_file = env.get("KONVERSIKU_REDIS_PASSWORD_FILE")
    if password_file:
        password_path: Path | None = Path(password_file).expanduser()
    else:
        default = DEV_ROOT / ".redis_password"
        password_path = default if default.is_file() else None
    return Settings(
        data_dir=Path(env.get("KONVERSIKU_DATA_DIR") or DEV_ROOT / "data").expanduser(),
        database_url=env.get("KONVERSIKU_DATABASE_URL") or "postgresql:///konversiku_dev",
        db_schema=env.get("KONVERSIKU_DB_SCHEMA") or "public",
        redis_url=env.get("KONVERSIKU_REDIS_URL") or "redis://127.0.0.1:6379/0",
        redis_password_file=password_path,
    )


def redis_password(settings: Settings) -> str | None:
    if settings.redis_password_file is None:
        return None
    try:
        value = settings.redis_password_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def broker_url(settings: Settings) -> str:
    """URL Redis lengkap dengan password dari file. Jangan dicetak atau di-log."""
    password = redis_password(settings)
    if not password:
        return settings.redis_url
    parts = urlsplit(settings.redis_url)
    host = parts.hostname or "127.0.0.1"
    netloc = f":{quote(password, safe='')}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
