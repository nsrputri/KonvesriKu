"""Folder data privat tahap 2.

Susunan: ``incoming/<acak>.part`` untuk unggahan yang sedang ditulis, dan
``jobs/<uuid>/input`` serta ``jobs/<uuid>/result``. Path selalu diturunkan dari UUID
tugas, tidak pernah dari masukan pengguna; metode menolak id yang bukan ``uuid.UUID``.
"""
from __future__ import annotations

import os
import secrets
import shutil
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import limits
from .common import ConversionError

PRIVATE = 0o700


def _private_dir(path: Path) -> None:
    path.mkdir(mode=PRIVATE, parents=True, exist_ok=True)
    os.chmod(path, PRIVATE)


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.incoming = self.root / "incoming"
        self.jobs = self.root / "jobs"

    def ensure(self) -> None:
        for path in (self.root, self.incoming, self.jobs):
            _private_dir(path)

    def _job_dir(self, job_id: uuid.UUID) -> Path:
        if not isinstance(job_id, uuid.UUID):
            raise TypeError("job_id harus uuid.UUID")
        return self.jobs / str(job_id)

    async def write_stream(self, chunks: AsyncIterator[bytes], max_bytes: int) -> tuple[Path, int]:
        """Tulis unggahan ke ``incoming/`` sedikit demi sedikit. File dihapus bila gagal."""
        self.ensure()
        part = self.incoming / f"{secrets.token_hex(16)}.part"
        size = 0
        try:
            with open(part, "xb") as handle:
                os.chmod(part, 0o600)
                async for chunk in chunks:
                    size += len(chunk)
                    if size > max_bytes:
                        raise ConversionError(
                            f"File melebihi batas {limits.size_label(max_bytes)}.", 413)
                    handle.write(chunk)
            if size == 0:
                raise ConversionError("File kosong. Pilih file berisi data.")
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return part, size

    def adopt_input(self, part: Path, job_id: uuid.UUID) -> Path:
        directory = self._job_dir(job_id)
        _private_dir(directory)
        target = directory / "input"
        os.replace(part, target)
        return target

    def read_input(self, job_id: uuid.UUID) -> bytes:
        return (self._job_dir(job_id) / "input").read_bytes()

    def result_path(self, job_id: uuid.UUID) -> Path:
        return self._job_dir(job_id) / "result"

    def write_result(self, job_id: uuid.UUID, data: bytes) -> int:
        """Tulis hasil secara atomik: file sementara, fsync, lalu rename menjadi ``result``."""
        directory = self._job_dir(job_id)
        _private_dir(directory)
        temporary = directory / f".result-{secrets.token_hex(8)}.tmp"
        try:
            with open(temporary, "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, directory / "result")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return len(data)

    def delete_input(self, job_id: uuid.UUID) -> None:
        (self._job_dir(job_id) / "input").unlink(missing_ok=True)

    def delete_job(self, job_id: uuid.UUID) -> None:
        shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

    def job_ids_on_disk(self) -> list[uuid.UUID]:
        if not self.jobs.is_dir():
            return []
        found = []
        for entry in self.jobs.iterdir():
            try:
                found.append(uuid.UUID(entry.name))
            except ValueError:
                continue
        return sorted(found)

    # Folder data dipakai bersamaan oleh API, pekerja, dan pembersihan: file bisa hilang atau
    # di-rename di antara listing dan stat. Entri yang hilang dilewati, bukan menjadi galat.

    def job_dir_age(self, job_id: uuid.UUID, now: datetime) -> timedelta:
        try:
            return now - _mtime(self._job_dir(job_id))
        except FileNotFoundError:
            return timedelta(0)

    def stale_incoming(self, now: datetime, age: timedelta) -> list[Path]:
        if not self.incoming.is_dir():
            return []
        stale = []
        for part in self.incoming.glob("*.part"):
            try:
                if _mtime(part) < now - age:
                    stale.append(part)
            except FileNotFoundError:
                continue
        return sorted(stale)

    def total_bytes(self) -> int:
        total = 0
        for directory, _subdirs, files in os.walk(self.root):  # os.walk melewati folder yang hilang
            for name in files:
                try:
                    total += os.stat(os.path.join(directory, name)).st_size
                except FileNotFoundError:
                    continue
        return total
