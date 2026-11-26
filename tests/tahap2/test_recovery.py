"""Pemulihan: lease habis, pekerja mulai ulang, pesan Redis hilang, kirim gagal."""
from __future__ import annotations

from datetime import timedelta

import pytest

from tahap2.bantuan_tahap2 import NOW, add_job, place_input

from konversiku import limits

LEASE = limits.LEASE_SECONDS

pytestmark = pytest.mark.layanan


def _claimed(conn, storage, worker_name: str, *, at=NOW):
    from konversiku import jobs

    job_id = add_job(conn, now=at)
    place_input(storage, job_id, b"a,b\n1,2\n")
    assert jobs.claim(conn, job_id, worker_name, at) is not None
    return job_id


def test_expired_lease_requeued_and_resent(conn, storage):
    from konversiku import jobs, recovery

    job_id = _claimed(conn, storage, "pekerja1@uji")
    sent = []
    later = NOW + timedelta(seconds=LEASE + 1)
    counts = recovery.recover(conn, storage, sent.append, later)
    assert counts == {"dikembalikan": 1, "digagalkan": 0, "dikirim_ulang": 0}
    assert sent == [job_id]
    job = jobs.get(conn, job_id)
    assert (job.status, job.worker, job.enqueued_at) == ("queued", None, later)


def test_lease_not_expired_is_left_alone(conn, storage):
    from konversiku import jobs, recovery

    job_id = _claimed(conn, storage, "pekerja1@uji")
    sent = []
    counts = recovery.recover(conn, storage, sent.append, NOW + timedelta(seconds=LEASE - 1))
    assert counts == {"dikembalikan": 0, "digagalkan": 0, "dikirim_ulang": 0}
    assert sent == []
    assert jobs.get(conn, job_id).status == "processing"


def test_expired_lease_without_attempts_left_fails(conn, storage):
    from konversiku import jobs, recovery, worker

    job_id = _claimed(conn, storage, "pekerja1@uji")
    conn.execute("UPDATE jobs SET attempts = 3 WHERE id = %s", (job_id,))
    conn.commit()
    sent = []
    later = NOW + timedelta(seconds=LEASE + 1)
    counts = recovery.recover(conn, storage, sent.append, later)
    assert counts == {"dikembalikan": 0, "digagalkan": 1, "dikirim_ulang": 0}
    assert sent == []
    job = jobs.get(conn, job_id)
    assert (job.status, job.error) == ("failed", worker.GENERIC_FAILURE)
    assert job.expires_at == later + timedelta(hours=24)
    assert not (storage.jobs / str(job_id) / "input").exists()


def test_restarted_worker_reclaims_only_own_jobs(conn, storage):
    from konversiku import jobs, recovery

    own = _claimed(conn, storage, "pekerja1@uji")
    other = _claimed(conn, storage, "pekerja2@uji")
    sent = []
    counts = recovery.recover(conn, storage, sent.append, NOW + timedelta(seconds=10),
                              worker_name="pekerja1@uji")
    assert counts == {"dikembalikan": 1, "digagalkan": 0, "dikirim_ulang": 0}
    assert sent == [own]
    assert jobs.get(conn, own).status == "queued"
    assert (jobs.get(conn, other).status, jobs.get(conn, other).worker) == ("processing", "pekerja2@uji")


def test_stale_queued_resent_after_two_minutes(conn, storage):
    from konversiku import jobs, recovery

    old = add_job(conn, now=NOW - timedelta(minutes=3))
    jobs.mark_enqueued(conn, old, NOW - timedelta(minutes=3))
    fresh = add_job(conn, now=NOW - timedelta(minutes=1))
    jobs.mark_enqueued(conn, fresh, NOW - timedelta(minutes=1))
    never_sent = add_job(conn, now=NOW - timedelta(minutes=5))
    conn.commit()
    sent = []
    counts = recovery.recover(conn, storage, sent.append, NOW)
    assert counts == {"dikembalikan": 0, "digagalkan": 0, "dikirim_ulang": 2}
    assert sorted(sent) == sorted([old, never_sent])


def test_send_failure_keeps_job_queued(conn, storage):
    from konversiku import jobs, recovery

    job_id = add_job(conn, now=NOW - timedelta(minutes=5))

    def broken_send(_job_id):
        raise ConnectionError("Redis mati")

    counts = recovery.recover(conn, storage, broken_send, NOW)
    assert counts == {"dikembalikan": 0, "digagalkan": 0, "dikirim_ulang": 0}
    assert jobs.get(conn, job_id).status == "queued"


def test_recovery_never_reverts_finished_job(conn, storage):
    """Lease habis tetapi pekerja sempat menyelesaikan tugas: status success tidak diubah."""
    from konversiku import jobs, recovery

    job_id = _claimed(conn, storage, "pekerja1@uji")
    stale = jobs.expired_leases(conn, NOW + timedelta(seconds=LEASE + 1))
    assert [job.id for job in stale] == [job_id]
    jobs.mark_success(conn, job_id, 5, [], NOW + timedelta(seconds=LEASE + 2))
    conn.commit()
    assert jobs.requeue(conn, job_id, NOW + timedelta(seconds=LEASE + 3)) is False
    assert jobs.mark_failed(conn, job_id, "x", NOW + timedelta(seconds=LEASE + 3)) is False
    conn.commit()
    assert jobs.get(conn, job_id).status == "success"
    assert recovery.recover(conn, storage, [].append, NOW + timedelta(seconds=400))["dikembalikan"] == 0
