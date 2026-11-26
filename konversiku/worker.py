"""Logika pekerja, terpisah dari Celery agar bisa dites langsung.

Aturan: semua ``ConversionError`` (file tidak valid, timeout alat, alat tidak ada, heap
Pandoc habis) langsung ``failed`` tanpa retry karena akan gagal lagi bila diulang. Retry
hanya untuk kegagalan di luar file (exception tak terduga, batas waktu Celery).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime

from . import jobs, registry, tools
from .common import ConversionError, ToolUnavailable

log = logging.getLogger("konversiku.worker")

GENERIC_FAILURE = ("Konversi gagal karena gangguan server setelah 3 percobaan. "
                   "Silakan unggah ulang.")


def _test_delay() -> None:
    """Kait uji (Task 9): jeda setelah klaim agar pekerja bisa dibunuh di tengah tugas."""
    if os.environ.get("KONVERSIKU_MODE_UJI") != "1":
        return
    seconds = float(os.environ.get("KONVERSIKU_UJI_JEDA_DETIK") or 0)
    if seconds > 0:
        time.sleep(seconds)


def _convert(job: jobs.Job, raw: bytes):
    pair = registry.get(job.source, job.target)
    if pair is None:
        raise ConversionError("Pasangan format tidak didukung.")
    reason = registry.unavailable_reason(pair, tools.detect())
    if reason:
        # Deteksi alat disimpan per proses; hasil gagal sesaat (mis. direktori kerja sempat
        # terhapus) tidak boleh menggagalkan semua tugas sampai pekerja di-restart.
        tools.detect.cache_clear()
        reason = registry.unavailable_reason(pair, tools.detect())
        if reason:
            log.warning("tugas %s: alat tidak tersedia setelah deteksi ulang", job.id)
            raise ToolUnavailable(reason)
    kwargs = {}
    if "formula_guard" in pair.options:
        kwargs["formula_guard"] = bool(job.options.get("formula_guard", True))
    result = pair.run(raw, **kwargs)
    return result, [*pair.notes, *result.notes]


def process_job(conn, storage, job_id: uuid.UUID, worker_name: str,
                now: Callable[[], datetime] = jobs.utcnow) -> str:
    """Kerjakan satu tugas. Hasil: ``success``, ``failed``, ``retry``, atau ``skipped``."""
    job = jobs.claim(conn, job_id, worker_name, now())
    if job is None:
        return "skipped"
    _test_delay()
    try:
        result, notes = _convert(job, storage.read_input(job.id))
        size = storage.write_result(job.id, result.data)
        if not jobs.mark_success(conn, job.id, size, notes, now()):
            # Tugas sudah diambil alih (pemulihan mengembalikannya ke antrean): jangan tandai
            # sukses dan jangan hapus input, karena percobaan berikutnya membutuhkannya.
            conn.commit()
            log.warning("tugas %s tidak lagi dimiliki %s; hasil tidak dicatat", job.id, worker_name)
            return "skipped"
        conn.commit()
    except ConversionError as exc:
        conn.rollback()
        jobs.mark_failed(conn, job.id, exc.message, now())
        conn.commit()
        storage.delete_input(job.id)
        return "failed"
    except Exception as exc:  # noqa: BLE001  (retry terbatas untuk kegagalan di luar file)
        conn.rollback()
        log.error("tugas %s gagal tak terduga (percobaan %d): %s",
                  job.id, job.attempts, type(exc).__name__)
        if job.attempts < job.max_attempts:
            jobs.requeue(conn, job.id, now())
            conn.commit()
            return "retry"
        jobs.mark_failed(conn, job.id, GENERIC_FAILURE, now())
        conn.commit()
        storage.delete_input(job.id)
        return "failed"
    storage.delete_input(job.id)
    return "success"
