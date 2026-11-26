"""Penyimpanan file privat tahap 2 (tanpa PostgreSQL/Redis; jalan di laptop dan VPS)."""
from __future__ import annotations

import asyncio
import os
import stat
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _stream(*chunks: bytes, fail_after: int | None = None):
    async def generator():
        for index, chunk in enumerate(chunks):
            if fail_after is not None and index == fail_after:
                raise OSError("koneksi putus")
            yield chunk
    return generator()


@pytest.fixture()
def storage(tmp_path):
    from konversiku.storage import Storage

    store = Storage(tmp_path / "data")
    store.ensure()
    return store


def _incoming(storage):
    return sorted(p.name for p in storage.incoming.iterdir())


def test_write_stream_stops_at_limit_and_removes_part(storage):
    from konversiku.common import ConversionError

    with pytest.raises(ConversionError) as caught:
        asyncio.run(storage.write_stream(_stream(b"a" * 6, b"b" * 6), max_bytes=10))
    assert caught.value.status_code == 413
    assert _incoming(storage) == []


def test_write_stream_rejects_empty(storage):
    from konversiku.common import ConversionError

    with pytest.raises(ConversionError) as caught:
        asyncio.run(storage.write_stream(_stream(b"", b""), max_bytes=10))
    assert caught.value.status_code == 400
    assert "kosong" in caught.value.message
    assert _incoming(storage) == []


def test_write_stream_removes_part_when_source_raises(storage):
    with pytest.raises(OSError):
        asyncio.run(storage.write_stream(_stream(b"abc", b"def", fail_after=1), max_bytes=100))
    assert _incoming(storage) == []


def test_write_stream_returns_part_and_size(storage):
    part, size = asyncio.run(storage.write_stream(_stream(b"abc", b"de"), max_bytes=100))
    assert size == 5
    assert part.parent == storage.incoming
    assert part.read_bytes() == b"abcde"


def test_adopt_input_moves_file(storage):
    part, _ = asyncio.run(storage.write_stream(_stream(b"isi"), max_bytes=100))
    job_id = uuid.uuid4()
    path = storage.adopt_input(part, job_id)
    assert not part.exists()
    assert path == storage.jobs / str(job_id) / "input"
    assert storage.read_input(job_id) == b"isi"
    assert storage.job_ids_on_disk() == [job_id]


def test_write_result_atomic(storage, monkeypatch):
    job_id = uuid.uuid4()
    assert storage.write_result(job_id, b"hasil") == 5
    assert storage.result_path(job_id).read_bytes() == b"hasil"

    other = uuid.uuid4()
    real_fsync = os.fsync

    def broken_fsync(fd):
        raise OSError("disk penuh")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    with pytest.raises(OSError):
        storage.write_result(other, b"setengah")
    monkeypatch.setattr(os, "fsync", real_fsync)
    assert not storage.result_path(other).exists()
    leftovers = [p.name for p in (storage.jobs / str(other)).iterdir()] if (storage.jobs / str(other)).exists() else []
    assert leftovers == []


def test_delete_input_and_job(storage):
    part, _ = asyncio.run(storage.write_stream(_stream(b"isi"), max_bytes=100))
    job_id = uuid.uuid4()
    storage.adopt_input(part, job_id)
    storage.write_result(job_id, b"hasil")
    storage.delete_input(job_id)
    assert not (storage.jobs / str(job_id) / "input").exists()
    assert storage.result_path(job_id).exists()
    storage.delete_input(job_id)  # aman diulang
    storage.delete_job(job_id)
    assert not (storage.jobs / str(job_id)).exists()
    storage.delete_job(job_id)  # aman diulang


@pytest.mark.parametrize("bad", ["../x", "..", str(uuid.uuid4()), None])
def test_job_id_must_be_uuid(storage, bad):
    with pytest.raises(TypeError):
        storage.result_path(bad)


@pytest.mark.skipif(sys.platform == "win32", reason="izin POSIX tidak berlaku di Windows")
def test_private_permissions(storage):
    for path in (storage.root, storage.incoming, storage.jobs):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    job_id = uuid.uuid4()
    storage.write_result(job_id, b"x")
    assert stat.S_IMODE((storage.jobs / str(job_id)).stat().st_mode) == 0o700


def test_total_bytes(storage):
    storage.write_result(uuid.uuid4(), b"12345")
    asyncio.run(storage.write_stream(_stream(b"abc"), max_bytes=100))
    assert storage.total_bytes() == 8


def test_total_bytes_ignores_file_vanishing_midway(storage, monkeypatch):
    """Saat folder dihitung, file lain bisa dihapus/di-rename oleh pekerja atau unggahan lain."""
    storage.write_result(uuid.uuid4(), b"12345")
    (storage.incoming / "hilang.part").write_bytes(b"abc")
    real_stat = os.stat

    def vanishing(path, *args, **kwargs):
        if str(path).endswith("hilang.part"):
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", vanishing)
    assert storage.total_bytes() == 5


def test_stale_incoming_and_job_age_ignore_vanished_entries(storage, monkeypatch):
    from pathlib import Path

    (storage.incoming / "hilang.part").write_bytes(b"abc")
    real_stat = Path.stat

    def vanishing(self, *args, **kwargs):
        if self.name == "hilang.part":
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanishing)
    later = datetime.now(timezone.utc) + timedelta(days=1)
    assert storage.stale_incoming(later, timedelta(hours=1)) == []
    assert storage.job_dir_age(uuid.uuid4(), later) == timedelta(0)


def test_stale_incoming_and_job_dir_age(storage):
    part, _ = asyncio.run(storage.write_stream(_stream(b"abc"), max_bytes=100))
    job_id = uuid.uuid4()
    storage.write_result(job_id, b"x")
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    os.utime(part, (old.timestamp(), old.timestamp()))
    os.utime(storage.jobs / str(job_id), (old.timestamp(), old.timestamp()))
    now = datetime.now(timezone.utc)
    assert storage.stale_incoming(now, timedelta(hours=1)) == [part]
    assert storage.stale_incoming(now, timedelta(hours=3)) == []
    age = storage.job_dir_age(job_id, now)
    assert timedelta(hours=1, minutes=59) < age < timedelta(hours=2, minutes=1)
