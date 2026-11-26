"""Pemulihan tugas: dijalankan berkala (tiap 1 menit) dan saat node pekerja mulai.

- Tanpa ``worker_name``: tugas ``processing`` yang lease-nya habis dikembalikan ke antrean
  (atau digagalkan bila percobaan habis), dan tugas ``queued`` yang tidak diambil lebih dari
  2 menit dikirim ulang (pesan Redis hilang atau API mati sebelum mengirim).
- Dengan ``worker_name``: hanya tugas ``processing`` milik node itu yang dikembalikan tanpa
  melihat lease, karena node yang baru mulai pasti tidak sedang mengerjakannya.

Pengiriman ulang aman karena klaim di PostgreSQL atomik. Perubahan status di-commit sebelum
``send`` dipanggil (``send_job`` membuka koneksi sendiri).
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from . import jobs
from .worker import GENERIC_FAILURE

log = logging.getLogger("konversiku.recovery")


def _try_send(send: Callable[[uuid.UUID], None], job_id: uuid.UUID) -> bool:
    try:
        send(job_id)
    except Exception as exc:  # noqa: BLE001  (Redis mati: tugas tetap queued, dicoba lagi nanti)
        log.warning("gagal mengirim tugas %s ke antrean: %s", job_id, type(exc).__name__)
        return False
    return True


def recover(conn, storage, send: Callable[[uuid.UUID], None], now: datetime,
            worker_name: str | None = None) -> dict[str, int]:
    counts = {"dikembalikan": 0, "digagalkan": 0, "dikirim_ulang": 0}
    stuck = (jobs.processing_by_worker(conn, worker_name) if worker_name
             else jobs.expired_leases(conn, now))
    requeued, failed = [], []
    for job in stuck:
        if job.attempts < job.max_attempts:
            if jobs.requeue(conn, job.id, now):
                requeued.append(job.id)
        elif jobs.mark_failed(conn, job.id, GENERIC_FAILURE, now):
            failed.append(job.id)
    conn.commit()
    for job_id in failed:
        storage.delete_input(job_id)
    counts["digagalkan"] = len(failed)
    for job_id in requeued:
        _try_send(send, job_id)
    counts["dikembalikan"] = len(requeued)
    if worker_name is None:
        stale = [job.id for job in jobs.stale_queued(conn, now)]
        conn.commit()
        counts["dikirim_ulang"] = sum(_try_send(send, job_id) for job_id in stale)
    return counts


def main() -> None:
    from . import db
    from .celery_app import send_job
    from .settings import load_settings
    from .storage import Storage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = load_settings()
    with db.connect(settings) as conn:
        counts = recover(conn, Storage(settings.data_dir), send_job, jobs.utcnow())
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
