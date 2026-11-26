"""Aplikasi Celery tahap 2. Redis hanya mengantar id tugas; status ada di PostgreSQL.

Jalankan pekerja: ``celery -A konversiku.celery_app worker -n pekerja1@%h -Q konversiku``.
Pesan Celery hanya berisi id tugas (UUID), tidak pernah kode tugas atau isi file.
"""
from __future__ import annotations

import logging
import uuid

from celery import Celery
from celery.signals import worker_ready

from . import limits
from .settings import broker_url, load_settings

QUEUE = "konversiku"
log = logging.getLogger("konversiku.celery")

app = Celery("konversiku")
app.conf.update(
    broker_url=broker_url(load_settings()),
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": limits.BROKER_VISIBILITY_TIMEOUT},
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=limits.TASK_SOFT_TIME_LIMIT,
    task_time_limit=limits.TASK_HARD_TIME_LIMIT,
    task_default_queue=QUEUE,
    task_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    worker_concurrency=limits.WORKER_CONCURRENCY,
)


@app.task(name="konversiku.konversi", bind=True)
def konversi(self, job_id: str) -> None:
    from . import db, jobs, worker
    from .storage import Storage

    settings = load_settings()
    job_uuid = uuid.UUID(job_id)
    storage = Storage(settings.data_dir)
    with db.connect(settings) as conn:
        outcome = worker.process_job(conn, storage, job_uuid, self.request.hostname)
        if outcome == "retry":
            job = jobs.get(conn, job_uuid)
            send_job(job_uuid, countdown=limits.RETRY_DELAY_SECONDS * job.attempts)


@worker_ready.connect
def recover_own_jobs(sender=None, **_kwargs) -> None:
    """Node yang baru mulai mengembalikan tugas processing miliknya (mati di tengah tugas)."""
    from . import db, jobs, recovery
    from .storage import Storage

    hostname = getattr(sender, "hostname", None)
    if not hostname:
        return
    try:
        settings = load_settings()
        with db.connect(settings) as conn:
            counts = recovery.recover(conn, Storage(settings.data_dir), send_job, jobs.utcnow(),
                                      worker_name=hostname)
        log.info("pemulihan saat mulai (%s): %s", hostname, counts)
    except Exception as exc:  # noqa: BLE001  (pemulihan berkala tetap menjadi cadangan)
        log.error("pemulihan saat mulai gagal (%s): %s", hostname, type(exc).__name__)


def send_job(job_id: uuid.UUID, countdown: int = 0) -> None:
    """Catat waktu kirim lalu kirim id ke antrean. Pemanggil sudah commit perubahan status."""
    from . import db, jobs

    with db.connect(load_settings()) as conn:
        jobs.mark_enqueued(conn, job_id, jobs.utcnow())
        conn.commit()
    konversi.apply_async(args=[str(job_id)], countdown=countdown, queue=QUEUE)
