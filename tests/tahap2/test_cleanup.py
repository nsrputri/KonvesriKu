"""Retensi PRD dengan jam palsu: hasil 24 jam setelah selesai, gagal 24 jam setelah gagal,
baris expired 7 hari, file yatim dan unggahan parsial setelah 1 jam."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tahap2.bantuan_tahap2 import NOW, add_job, place_input

pytestmark = pytest.mark.layanan


def _finished(conn, storage, *, status: str, created, finished):
    from konversiku import jobs

    job_id = add_job(conn, now=created)
    place_input(storage, job_id, b"a,b\n1,2\n")
    jobs.claim(conn, job_id, "pekerja1@uji", created)
    if status == "success":
        storage.write_result(job_id, b"hasil")
        jobs.mark_success(conn, job_id, 5, [], finished)
    else:
        jobs.mark_failed(conn, job_id, "File rusak.", finished)
    conn.commit()
    storage.delete_input(job_id)
    return job_id


def _age(path, now, minutes: int) -> None:
    moment = (now - timedelta(minutes=minutes)).timestamp()
    os.utime(path, (moment, moment))


def test_result_removed_24h_after_finish_not_upload(conn, storage):
    from konversiku import cleanup, jobs

    job_id = _finished(conn, storage, status="success", created=NOW, finished=NOW + timedelta(hours=5))
    counts = cleanup.cleanup(conn, storage, NOW + timedelta(hours=28, minutes=59))
    assert counts["kedaluwarsa"] == 0
    assert jobs.get(conn, job_id).status == "success"
    assert storage.result_path(job_id).exists()

    counts = cleanup.cleanup(conn, storage, NOW + timedelta(hours=29, minutes=1))
    assert counts["kedaluwarsa"] == 1
    assert jobs.get(conn, job_id).status == "expired"
    assert not (storage.jobs / str(job_id)).exists()


def test_failed_job_cleaned_24h_after_failure(conn, storage):
    from konversiku import cleanup, jobs

    job_id = _finished(conn, storage, status="failed", created=NOW, finished=NOW + timedelta(hours=1))
    assert cleanup.cleanup(conn, storage, NOW + timedelta(hours=24, minutes=59))["kedaluwarsa"] == 0
    assert jobs.get(conn, job_id).status == "failed"
    assert cleanup.cleanup(conn, storage, NOW + timedelta(hours=25, minutes=1))["kedaluwarsa"] == 1
    assert jobs.get(conn, job_id).status == "expired"
    assert not (storage.jobs / str(job_id)).exists()


def test_expired_rows_deleted_after_7_days(conn, storage):
    from konversiku import cleanup, jobs

    job_id = _finished(conn, storage, status="success", created=NOW, finished=NOW)
    expires = NOW + timedelta(hours=24)
    cleanup.cleanup(conn, storage, expires + timedelta(minutes=1))
    kept = cleanup.cleanup(conn, storage, expires + timedelta(days=7) - timedelta(minutes=1))
    assert kept["baris_dihapus"] == 0
    assert jobs.get(conn, job_id).status == "expired"
    gone = cleanup.cleanup(conn, storage, expires + timedelta(days=7, minutes=1))
    assert gone["baris_dihapus"] == 1
    assert jobs.get(conn, job_id) is None


def test_orphan_job_dir_removed_after_one_hour(conn, storage):
    from konversiku import cleanup

    now = datetime.now(timezone.utc)
    young, old = uuid.uuid4(), uuid.uuid4()
    for job_id, minutes in ((young, 59), (old, 61)):
        storage.write_result(job_id, b"yatim")
        _age(storage.jobs / str(job_id), now, minutes)
    counts = cleanup.cleanup(conn, storage, now)
    assert counts["yatim_dihapus"] == 1
    assert (storage.jobs / str(young)).exists()
    assert not (storage.jobs / str(old)).exists()


def test_stale_part_removed_after_one_hour(conn, storage):
    from konversiku import cleanup

    now = datetime.now(timezone.utc)
    young, old = storage.incoming / "muda.part", storage.incoming / "tua.part"
    for part, minutes in ((young, 59), (old, 61)):
        part.write_bytes(b"sebagian")
        _age(part, now, minutes)
    counts = cleanup.cleanup(conn, storage, now)
    assert counts["yatim_dihapus"] == 1
    assert young.exists()
    assert not old.exists()


def test_active_jobs_untouched(conn, storage):
    from konversiku import cleanup, jobs

    queued = add_job(conn)
    place_input(storage, queued, b"a,b\n")
    processing = add_job(conn)
    place_input(storage, processing, b"a,b\n")
    jobs.claim(conn, processing, "pekerja1@uji", NOW)
    far = datetime.now(timezone.utc) + timedelta(days=30)
    counts = cleanup.cleanup(conn, storage, far)
    assert counts == {"kedaluwarsa": 0, "baris_dihapus": 0, "yatim_dihapus": 0}
    assert jobs.get(conn, queued).status == "queued"
    assert jobs.get(conn, processing).status == "processing"
    assert (storage.jobs / str(queued) / "input").exists()
    assert (storage.jobs / str(processing) / "input").exists()


def test_expired_row_with_leftover_files_is_cleaned(conn, storage):
    """Crash setelah status expired di-commit tetapi sebelum file dihapus: file tetap dibersihkan."""
    from konversiku import cleanup, jobs

    job_id = _finished(conn, storage, status="success", created=NOW, finished=NOW)
    jobs.mark_expired(conn, job_id)
    conn.commit()
    now = datetime.now(timezone.utc)
    _age(storage.jobs / str(job_id), now, 61)
    assert cleanup.cleanup(conn, storage, now)["yatim_dihapus"] == 1
    assert not (storage.jobs / str(job_id)).exists()
