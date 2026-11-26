"""Konstanta dan pembuat data bersama untuk tes tahap 2 (bukan fixture)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

NOW = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)


class FakeClock:
    """Jam palsu: mengembalikan nilai berurutan, lalu nilai terakhir terus-menerus."""

    def __init__(self, *values: datetime):
        self.values = list(values) or [NOW]
        self.calls = 0

    def __call__(self) -> datetime:
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value


def place_input(storage, job_id: uuid.UUID, data: bytes) -> None:
    """Taruh input tugas seperti setelah unggahan selesai."""
    storage.ensure()
    part = storage.incoming / f"uji-{job_id}.part"
    part.write_bytes(data)
    storage.adopt_input(part, job_id)


def add_job(conn, *, source: str = "csv", target: str = "json", now: datetime = NOW,
            options: dict | None = None, input_bytes: int = 10) -> uuid.UUID:
    """Sisipkan satu tugas ``queued`` lalu commit; kembalikan id-nya."""
    from konversiku import jobs

    job_id = uuid.uuid4()
    _, hashed = jobs.new_code()
    jobs.insert_job(conn, job_id=job_id, code_hash=hashed, source=source, target=target,
                    options=options or {}, input_bytes=input_bytes, now=now)
    conn.commit()
    return job_id
