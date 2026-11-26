"""Pembersihan berkala (tiap 10 menit) sesuai retensi PRD.

- ``success`` dan ``failed`` yang ``expires_at``-nya lewat (24 jam setelah selesai/gagal):
  status ``expired`` di-commit dulu, lalu folder tugas dihapus.
- Baris ``expired`` dihapus 7 hari setelah kedaluwarsa (baris hanya berisi hash, format, waktu).
- File yatim berumur lebih dari 1 jam dihapus: unggahan parsial ``incoming/*.part`` dan
  folder tugas tanpa baris aktif (termasuk sisa tugas expired bila penghapusan sempat gagal).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from . import jobs, limits

log = logging.getLogger("konversiku.cleanup")


def cleanup(conn, storage, now: datetime) -> dict[str, int]:
    counts = {"kedaluwarsa": 0, "baris_dihapus": 0, "yatim_dihapus": 0}

    due = jobs.due_for_expiry(conn, now)
    for job in due:
        jobs.mark_expired(conn, job.id)
    conn.commit()
    for job in due:
        storage.delete_job(job.id)
    counts["kedaluwarsa"] = len(due)

    counts["baris_dihapus"] = jobs.delete_expired_rows(conn, now)
    conn.commit()

    orphan_age = timedelta(seconds=limits.ORPHAN_AGE_SECONDS)
    for part in storage.stale_incoming(now, orphan_age):
        part.unlink(missing_ok=True)
        counts["yatim_dihapus"] += 1
    on_disk = storage.job_ids_on_disk()
    alive = jobs.existing_ids(conn, on_disk, include_expired=False)
    conn.commit()
    for job_id in on_disk:
        if job_id not in alive and storage.job_dir_age(job_id, now) > orphan_age:
            storage.delete_job(job_id)
            counts["yatim_dihapus"] += 1
    return counts


def main() -> None:
    from . import db
    from .settings import load_settings
    from .storage import Storage

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = load_settings()
    storage = Storage(settings.data_dir)
    storage.ensure()
    with db.connect(settings) as conn:
        counts = cleanup(conn, storage, jobs.utcnow())
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
