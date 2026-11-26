"""Repositori tugas di PostgreSQL: kode tugas, klaim atomik, transisi status.

Semua waktu diberikan pemanggil (``now``) agar tes bisa memakai jam palsu. Fungsi tulis
tidak commit sendiri, kecuali ``claim`` (klaim harus langsung terlihat proses lain).
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import limits

STATUSES = ("queued", "processing", "success", "failed", "expired")
_CODE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_COLUMNS = ("id, status, source, target, options, attempts, max_attempts, worker, lease_until, "
            "error, notes, input_bytes, output_bytes, created_at, enqueued_at, started_at, "
            "finished_at, expires_at")


@dataclass
class Job:
    id: uuid.UUID
    status: str
    source: str
    target: str
    options: dict
    attempts: int
    max_attempts: int
    worker: str | None
    lease_until: datetime | None
    error: str | None
    notes: list
    input_bytes: int
    output_bytes: int | None
    created_at: datetime
    enqueued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    expires_at: datetime | None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_code() -> tuple[str, bytes]:
    code = secrets.token_urlsafe(32)
    return code, hash_code(code)


def hash_code(code: str) -> bytes:
    return hashlib.sha256(code.encode("ascii")).digest()


def normalize_code(raw: str | None) -> str | None:
    """Kode bersih (spasi di ujung dibuang) atau None bila bentuknya tidak mungkin benar."""
    if raw is None:
        return None
    code = raw.strip()
    return code if _CODE_RE.fullmatch(code) else None


def _one(conn, query: str, params) -> Job | None:
    row = conn.execute(query, params).fetchone()
    return Job(**row) if row else None


def _many(conn, query: str, params) -> list[Job]:
    return [Job(**row) for row in conn.execute(query, params).fetchall()]


def _retention() -> timedelta:
    return timedelta(hours=limits.RETENTION_HOURS)


def insert_job(conn, *, job_id: uuid.UUID, code_hash: bytes, source: str, target: str,
               options: dict, input_bytes: int, now: datetime) -> None:
    from psycopg.types.json import Jsonb

    conn.execute(
        "INSERT INTO jobs (id, code_hash, source, target, options, input_bytes, created_at, "
        "max_attempts) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (job_id, code_hash, source, target, Jsonb(options), input_bytes, now, limits.MAX_ATTEMPTS),
    )


def find_by_code_hash(conn, code_hash: bytes) -> Job | None:
    return _one(conn, f"SELECT {_COLUMNS} FROM jobs WHERE code_hash = %s", (code_hash,))


def get(conn, job_id: uuid.UUID) -> Job | None:
    return _one(conn, f"SELECT {_COLUMNS} FROM jobs WHERE id = %s", (job_id,))


def claim(conn, job_id: uuid.UUID, worker: str, now: datetime) -> Job | None:
    """Ambil tugas queued secara atomik. None bila sudah diambil atau percobaan habis."""
    job = _one(
        conn,
        "UPDATE jobs SET status = 'processing', attempts = attempts + 1, worker = %s, "
        "lease_until = %s, started_at = %s "
        "WHERE id = %s AND status = 'queued' AND attempts < max_attempts "
        f"RETURNING {_COLUMNS}",
        (worker, now + timedelta(seconds=limits.LEASE_SECONDS), now, job_id),
    )
    conn.commit()
    return job


def _count_outcome(conn, outcome: str) -> None:
    conn.execute("UPDATE job_totals SET jumlah = jumlah + 1 WHERE hasil = %s", (outcome,))


def mark_success(conn, job_id: uuid.UUID, output_bytes: int, notes: list, now: datetime) -> bool:
    """Tandai sukses tugas yang masih processing dan tambah total. False bila statusnya sudah
    berubah (mis. pemulihan mengembalikannya ke antrean): pemanggil tidak memilikinya lagi."""
    from psycopg.types.json import Jsonb

    cursor = conn.execute(
        "UPDATE jobs SET status = 'success', output_bytes = %s, notes = %s, error = NULL, "
        "finished_at = %s, expires_at = %s, lease_until = NULL WHERE id = %s AND status = 'processing'",
        (output_bytes, Jsonb(list(notes)), now, now + _retention(), job_id),
    )
    if cursor.rowcount != 1:
        return False
    _count_outcome(conn, "success")
    return True


def mark_failed(conn, job_id: uuid.UUID, error: str, now: datetime) -> bool:
    """Gagalkan tugas yang masih processing dan tambah total. False bila statusnya sudah berubah."""
    cursor = conn.execute(
        "UPDATE jobs SET status = 'failed', error = %s, finished_at = %s, expires_at = %s, "
        "lease_until = NULL WHERE id = %s AND status = 'processing'",
        (error, now, now + _retention(), job_id),
    )
    if cursor.rowcount != 1:
        return False
    _count_outcome(conn, "failed")
    return True


def totals(conn) -> dict[str, int]:
    """Total tugas berhasil dan gagal sepanjang waktu (tidak berkurang oleh retensi)."""
    counts = {"success": 0, "failed": 0}
    for row in conn.execute("SELECT hasil, jumlah FROM job_totals").fetchall():
        counts[row["hasil"]] = int(row["jumlah"])
    return counts


def requeue(conn, job_id: uuid.UUID, now: datetime) -> bool:
    """Kembalikan tugas processing ke antrean. False bila statusnya sudah berubah."""
    cursor = conn.execute(
        "UPDATE jobs SET status = 'queued', worker = NULL, lease_until = NULL, enqueued_at = %s "
        "WHERE id = %s AND status = 'processing'",
        (now, job_id),
    )
    return cursor.rowcount == 1


def mark_enqueued(conn, job_id: uuid.UUID, now: datetime) -> None:
    conn.execute("UPDATE jobs SET enqueued_at = %s WHERE id = %s", (now, job_id))


def queue_position(conn, job: Job) -> int | None:
    if job.status != "queued":
        return None
    row = conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE status = 'queued' AND (created_at, id) <= (%s, %s)",
        (job.created_at, job.id),
    ).fetchone()
    return int(row["n"])


def count_queued(conn) -> int:
    return int(conn.execute("SELECT count(*) AS n FROM jobs WHERE status = 'queued'").fetchone()["n"])


def count_by_status(conn) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for row in conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall():
        counts[row["status"]] = int(row["n"])
    return counts


def expired_leases(conn, now: datetime) -> list[Job]:
    return _many(conn, f"SELECT {_COLUMNS} FROM jobs WHERE status = 'processing' AND lease_until < %s",
                 (now,))


def processing_by_worker(conn, worker: str) -> list[Job]:
    return _many(conn, f"SELECT {_COLUMNS} FROM jobs WHERE status = 'processing' AND worker = %s",
                 (worker,))


def stale_queued(conn, now: datetime) -> list[Job]:
    """Queued yang belum diambil: dikirim lebih dari REQUEUE_AFTER lalu, atau belum pernah dikirim."""
    limit = now - timedelta(seconds=limits.REQUEUE_AFTER_SECONDS)
    return _many(
        conn,
        f"SELECT {_COLUMNS} FROM jobs WHERE status = 'queued' AND "
        "((enqueued_at IS NULL AND created_at < %s) OR enqueued_at < %s)",
        (limit, limit),
    )


def due_for_expiry(conn, now: datetime) -> list[Job]:
    return _many(
        conn,
        f"SELECT {_COLUMNS} FROM jobs WHERE status IN ('success', 'failed') AND expires_at <= %s",
        (now,),
    )


def mark_expired(conn, job_id: uuid.UUID) -> None:
    conn.execute("UPDATE jobs SET status = 'expired' WHERE id = %s", (job_id,))


def delete_expired_rows(conn, now: datetime) -> int:
    limit = now - timedelta(days=limits.EXPIRED_ROW_KEEP_DAYS)
    cursor = conn.execute("DELETE FROM jobs WHERE status = 'expired' AND expires_at < %s", (limit,))
    return cursor.rowcount


def existing_ids(conn, ids: list[uuid.UUID], include_expired: bool = True) -> set[uuid.UUID]:
    """Id yang punya baris. Dengan ``include_expired=False``, baris expired tidak dihitung
    (file tugas expired boleh dibersihkan)."""
    if not ids:
        return set()
    query = "SELECT id FROM jobs WHERE id = ANY(%s)"
    if not include_expired:
        query += " AND status <> 'expired'"
    rows = conn.execute(query, (list(ids),)).fetchall()
    return {row["id"] for row in rows}
