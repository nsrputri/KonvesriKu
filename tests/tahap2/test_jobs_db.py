"""Repositori tugas di PostgreSQL: kode, klaim atomik, transisi status, retensi."""
from __future__ import annotations

import re
import threading
import uuid
from datetime import timedelta

import pytest
from tahap2.bantuan_tahap2 import NOW, add_job

from konversiku import limits

layanan = pytest.mark.layanan


def test_new_code_shape():
    from konversiku import jobs

    kode, hashed = jobs.new_code()
    assert len(kode) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", kode)
    assert jobs.hash_code(kode) == hashed
    assert len(hashed) == 32
    assert jobs.normalize_code(" " + kode + "\n") == kode
    assert jobs.normalize_code("../etc") is None
    assert jobs.normalize_code("") is None
    assert jobs.normalize_code(None) is None
    assert jobs.normalize_code("a" * 200) is None


@layanan
def test_insert_and_find_does_not_store_code(conn):
    from konversiku import jobs

    kode, hashed = jobs.new_code()
    job_id = uuid.uuid4()
    jobs.insert_job(conn, job_id=job_id, code_hash=hashed, source="csv", target="json",
                    options={"formula_guard": True}, input_bytes=12, now=NOW)
    conn.commit()
    found = jobs.find_by_code_hash(conn, hashed)
    assert found.id == job_id
    assert found.status == "queued"
    assert found.options == {"formula_guard": True}
    assert found.created_at == NOW
    dumped = conn.execute("SELECT row_to_json(j)::text AS t FROM jobs j").fetchone()["t"]
    assert kode not in dumped
    assert jobs.find_by_code_hash(conn, jobs.hash_code("x" * 43)) is None


@layanan
def test_claim_moves_to_processing(conn):
    from konversiku import jobs

    job_id = add_job(conn)
    claimed = jobs.claim(conn, job_id, "pekerja1@uji", NOW)
    assert claimed.status == "processing"
    assert claimed.attempts == 1
    assert claimed.worker == "pekerja1@uji"
    assert claimed.started_at == NOW
    assert claimed.lease_until == NOW + timedelta(seconds=limits.LEASE_SECONDS)


@layanan
def test_second_claim_returns_none(conn):
    from konversiku import jobs

    job_id = add_job(conn)
    assert jobs.claim(conn, job_id, "pekerja1@uji", NOW) is not None
    assert jobs.claim(conn, job_id, "pekerja2@uji", NOW) is None


@layanan
def test_concurrent_claims_only_one_wins(conn, connect_again):
    from konversiku import jobs

    ids = [add_job(conn) for _ in range(20)]
    barrier = threading.Barrier(2)
    won: dict[str, list[uuid.UUID]] = {"a": [], "b": []}

    def run(name: str):
        own = connect_again()
        barrier.wait()
        for job_id in ids:
            if jobs.claim(own, job_id, f"{name}@uji", NOW) is not None:
                won[name].append(job_id)

    threads = [threading.Thread(target=run, args=(name,)) for name in won]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    together = won["a"] + won["b"]
    assert sorted(together) == sorted(ids)
    assert len(set(together)) == 20


@layanan
def test_claim_refused_when_attempts_exhausted(conn):
    from konversiku import jobs

    job_id = add_job(conn)
    conn.execute("UPDATE jobs SET attempts = 3 WHERE id = %s", (job_id,))
    conn.commit()
    assert jobs.claim(conn, job_id, "pekerja1@uji", NOW) is None


@layanan
def test_mark_success_expires_24h_after_finish(conn):
    from konversiku import jobs

    job_id = add_job(conn)
    jobs.claim(conn, job_id, "pekerja1@uji", NOW)
    done = NOW + timedelta(hours=5)
    jobs.mark_success(conn, job_id, 99, ["catatan"], done)
    conn.commit()
    job = jobs.get(conn, job_id)
    assert job.status == "success"
    assert job.finished_at == done
    assert job.expires_at == done + timedelta(hours=24)
    assert job.output_bytes == 99
    assert job.notes == ["catatan"]
    assert job.lease_until is None


@layanan
def test_mark_success_only_from_processing_and_counted_once(conn):
    from konversiku import jobs

    queued = add_job(conn)
    assert jobs.mark_success(conn, queued, 5, [], NOW) is False
    conn.commit()
    assert jobs.get(conn, queued).status == "queued"
    job_id = add_job(conn)
    jobs.claim(conn, job_id, "pekerja1@uji", NOW)
    assert jobs.mark_success(conn, job_id, 5, [], NOW) is True
    assert jobs.mark_success(conn, job_id, 5, [], NOW) is False
    assert jobs.mark_failed(conn, job_id, "x", NOW) is False
    conn.commit()
    assert jobs.totals(conn) == {"success": 1, "failed": 0}


@layanan
def test_mark_failed_expires_24h(conn):
    from konversiku import jobs

    job_id = add_job(conn)
    jobs.claim(conn, job_id, "pekerja1@uji", NOW)
    jobs.mark_failed(conn, job_id, "File rusak.", NOW)
    conn.commit()
    job = jobs.get(conn, job_id)
    assert job.status == "failed"
    assert job.error == "File rusak."
    assert job.finished_at == NOW
    assert job.expires_at == NOW + timedelta(hours=24)


@layanan
def test_queue_position(conn):
    from konversiku import jobs

    ids = [add_job(conn, now=NOW + timedelta(seconds=i)) for i in range(3)]
    positions = [jobs.queue_position(conn, jobs.get(conn, job_id)) for job_id in ids]
    assert positions == [1, 2, 3]
    jobs.claim(conn, ids[0], "pekerja1@uji", NOW)
    assert jobs.queue_position(conn, jobs.get(conn, ids[0])) is None
    assert jobs.queue_position(conn, jobs.get(conn, ids[2])) == 2
    assert jobs.count_queued(conn) == 2
