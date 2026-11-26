"""Pekerja: klaim, konversi lewat mesin tahap 1, status akhir, retry terbatas."""
from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest
from conftest import RECORDS, data_sample, records_of

from tahap2.bantuan_tahap2 import NOW, FakeClock, add_job, place_input

layanan = pytest.mark.layanan


def _job_with_input(conn, storage, source: str, target: str, data: bytes):
    job_id = add_job(conn, source=source, target=target, input_bytes=len(data))
    place_input(storage, job_id, data)
    return job_id


def _input_exists(storage, job_id) -> bool:
    return (storage.jobs / str(job_id) / "input").exists()


def _patch_run(monkeypatch, source: str, target: str, run):
    from konversiku import registry, worker

    real_get = registry.get

    def fake_get(src, tgt):
        pair = real_get(src, tgt)
        if (src, tgt) == (source, target):
            return dataclasses.replace(pair, run=run)
        return pair

    monkeypatch.setattr(worker.registry, "get", fake_get)


@layanan
def test_success_writes_result_and_deletes_input(conn, storage):
    from konversiku import jobs, worker

    job_id = _job_with_input(conn, storage, "csv", "json", data_sample("csv"))
    clock = FakeClock(NOW, NOW + timedelta(seconds=3))
    assert worker.process_job(conn, storage, job_id, "pekerja1@uji", now=clock) == "success"
    job = jobs.get(conn, job_id)
    assert job.status == "success"
    assert job.attempts == 1
    assert job.finished_at == NOW + timedelta(seconds=3)
    assert job.expires_at == job.finished_at + timedelta(hours=24)
    result = storage.result_path(job_id).read_bytes()
    assert job.output_bytes == len(result)
    assert records_of("json", result) == RECORDS
    assert not _input_exists(storage, job_id)


@layanan
def test_invalid_file_fails_without_retry(conn, storage):
    from konversiku import jobs, worker

    job_id = _job_with_input(conn, storage, "json", "csv", b'{"rusak": ')
    assert worker.process_job(conn, storage, job_id, "pekerja1@uji", now=FakeClock()) == "failed"
    job = jobs.get(conn, job_id)
    assert job.status == "failed"
    assert job.attempts == 1
    assert job.error and job.error != worker.GENERIC_FAILURE
    assert job.expires_at == NOW + timedelta(hours=24)
    assert not _input_exists(storage, job_id)
    assert not storage.result_path(job_id).exists()


@layanan
def test_tool_timeout_fails_without_retry(conn, storage, monkeypatch):
    from konversiku import jobs, worker
    from konversiku.common import ConversionTimeout

    def slow(raw, **kwargs):
        raise ConversionTimeout("Konversi melewati batas waktu.")

    _patch_run(monkeypatch, "csv", "json", slow)
    job_id = _job_with_input(conn, storage, "csv", "json", data_sample("csv"))
    assert worker.process_job(conn, storage, job_id, "pekerja1@uji", now=FakeClock()) == "failed"
    job = jobs.get(conn, job_id)
    assert (job.status, job.attempts, job.error) == ("failed", 1, "Konversi melewati batas waktu.")


def _flaky_tools(monkeypatch, *, recovers: bool):
    """Deteksi alat yang gagal sampai cache dikosongkan (recovers=True) atau selalu gagal."""
    from konversiku import registry, tools, worker

    calls = {"clear": 0}
    real_reason = registry.unavailable_reason
    real_clear = tools.detect.cache_clear

    def reason(pair, found):
        if recovers and calls["clear"] > 0:
            return real_reason(pair, found)
        return "Pandoc belum terpasang di server"

    def clear():
        calls["clear"] += 1
        real_clear()

    monkeypatch.setattr(worker.registry, "unavailable_reason", reason)
    monkeypatch.setattr(tools.detect, "cache_clear", clear)
    return calls


@layanan
def test_tool_detection_retried_once_before_failing(conn, storage, monkeypatch):
    """Hasil deteksi alat yang gagal sesaat tidak boleh tersimpan selamanya di pekerja."""
    from konversiku import jobs, worker

    calls = _flaky_tools(monkeypatch, recovers=True)
    job_id = _job_with_input(conn, storage, "csv", "json", data_sample("csv"))
    assert worker.process_job(conn, storage, job_id, "pekerja1@uji", now=FakeClock()) == "success"
    assert calls["clear"] == 1
    assert jobs.get(conn, job_id).status == "success"


@layanan
def test_missing_tool_still_fails_after_one_redetection(conn, storage, monkeypatch):
    from konversiku import jobs, worker

    calls = _flaky_tools(monkeypatch, recovers=False)
    job_id = _job_with_input(conn, storage, "csv", "json", data_sample("csv"))
    assert worker.process_job(conn, storage, job_id, "pekerja1@uji", now=FakeClock()) == "failed"
    assert calls["clear"] == 1
    job = jobs.get(conn, job_id)
    assert (job.status, job.attempts, job.error) == ("failed", 1, "Pandoc belum terpasang di server")


@layanan
def test_unexpected_error_retries_then_fails(conn, storage, monkeypatch):
    from konversiku import jobs, worker

    def broken(raw, **kwargs):
        raise RuntimeError("bug tak terduga")

    _patch_run(monkeypatch, "csv", "json", broken)
    job_id = _job_with_input(conn, storage, "csv", "json", data_sample("csv"))
    outcomes = []
    for _ in range(3):
        outcomes.append(worker.process_job(conn, storage, job_id, "pekerja1@uji", now=FakeClock()))
        if outcomes[-1] == "retry":
            job = jobs.get(conn, job_id)
            assert job.status == "queued"
            assert job.worker is None
            assert _input_exists(storage, job_id)
    assert outcomes == ["retry", "retry", "failed"]
    job = jobs.get(conn, job_id)
    assert (job.status, job.attempts, job.error) == ("failed", 3, worker.GENERIC_FAILURE)
    assert not _input_exists(storage, job_id)


@layanan
def test_lost_ownership_does_not_mark_success_or_delete_input(conn, storage, connect_again, monkeypatch):
    """Pemulihan mengembalikan tugas ke antrean saat pekerja masih mengonversi: pekerja lama
    tidak boleh menandai success atau menghapus input yang dibutuhkan percobaan berikutnya."""
    from konversiku import jobs, worker
    from konversiku.common import Result

    other = connect_again()

    def converted_after_takeover(raw, **kwargs):
        assert jobs.requeue(other, job_id, NOW) is True
        other.commit()
        return Result(b"[]", [])

    _patch_run(monkeypatch, "csv", "json", converted_after_takeover)
    job_id = _job_with_input(conn, storage, "csv", "json", data_sample("csv"))
    assert worker.process_job(conn, storage, job_id, "pekerja1@uji", now=FakeClock()) == "skipped"
    job = jobs.get(conn, job_id)
    assert job.status == "queued"
    assert _input_exists(storage, job_id)
    assert jobs.totals(conn) == {"success": 0, "failed": 0}


@layanan
def test_already_claimed_is_skipped(conn, storage):
    from konversiku import jobs, worker

    job_id = _job_with_input(conn, storage, "csv", "json", data_sample("csv"))
    jobs.claim(conn, job_id, "pekerja2@uji", NOW)
    assert worker.process_job(conn, storage, job_id, "pekerja1@uji", now=FakeClock()) == "skipped"
    job = jobs.get(conn, job_id)
    assert (job.status, job.worker, job.attempts) == ("processing", "pekerja2@uji", 1)
    assert _input_exists(storage, job_id)


def test_celery_settings():
    from konversiku import limits
    from konversiku.celery_app import QUEUE, app

    conf = app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_ignore_result is True
    assert conf.task_soft_time_limit == 600
    assert conf.task_time_limit == 660
    assert conf.worker_concurrency == 2
    assert conf.task_default_queue == QUEUE == "konversiku"
    assert conf.broker_transport_options["visibility_timeout"] == 3600
    assert conf.broker_transport_options["visibility_timeout"] > conf.task_time_limit
    assert limits.LEASE_SECONDS > conf.task_time_limit
    assert "konversiku.konversi" in app.tasks
