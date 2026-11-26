"""API tugas tahap 2: submit, status, unduh.

Kode tugas hanya lewat header ``X-Kode-Tugas``, tidak pernah di URL atau query, karena
log akses uvicorn dan Nginx mencatat URL. Dependensi ``get_conn``, ``get_storage``, dan
``get_sender`` dapat ditimpa tes lewat ``app.dependency_overrides``.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

import anyio
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.requests import ClientDisconnect

from . import jobs, limits, registry, tools
from .common import ConversionError
from .settings import load_settings
from .storage import Storage
from .web import DOWNLOAD_HEADERS, NO_STORE, flag

router = APIRouter()
log = logging.getLogger("konversiku.api")

CODE_HEADER = "X-Kode-Tugas"
INVALID_CODE = "Kode tugas tidak valid."
NOT_FOUND = "Kode tugas tidak ditemukan."
NOT_DONE = "Tugas belum selesai."
EXPIRED = "Hasil sudah kedaluwarsa dan dihapus."
QUEUE_FULL = "Antrean sedang penuh. Coba lagi beberapa menit lagi."
QUOTA_FULL = "Penyimpanan server sedang penuh. Coba lagi nanti."
SERVICE_DOWN = "Layanan tugas sedang tidak tersedia. Coba lagi nanti."
UNSUPPORTED = "Pasangan format tidak didukung. Lihat daftar di Bantuan atau /api/formats."


def get_conn():
    from . import db

    conn = db.connect(load_settings())
    try:
        yield conn
    finally:
        conn.close()


def get_storage() -> Storage:
    storage = Storage(load_settings().data_dir)
    storage.ensure()
    return storage


def get_sender():
    from .celery_app import send_job

    return send_job


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _too_large(source: str, limit: int) -> ConversionError:
    return ConversionError(f"File {source.upper()} melebihi batas {limits.size_label(limit)}.", 413)


def _check_capacity(conn, storage: Storage) -> None:
    queued = jobs.count_queued(conn)
    conn.commit()
    if queued >= limits.MAX_QUEUED_JOBS:
        raise ConversionError(QUEUE_FULL, 503)
    if storage.total_bytes() >= limits.DATA_QUOTA_BYTES:
        raise ConversionError(QUOTA_FULL, 503)


def _store(conn, storage: Storage, part, job_id: uuid.UUID, code_hash: bytes, pair, options: dict,
           size: int) -> None:
    """Pindahkan input dan simpan baris dalam satu langkah; bila gagal, tidak ada sisa."""
    try:
        storage.adopt_input(part, job_id)
        jobs.insert_job(conn, job_id=job_id, code_hash=code_hash, source=pair.source,
                        target=pair.target, options=options, input_bytes=size, now=jobs.utcnow())
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001  (koneksi sudah rusak; file tetap dibersihkan)
            pass
        part.unlink(missing_ok=True)
        storage.delete_job(job_id)
        raise


def _send(send, job_id: uuid.UUID) -> None:
    try:
        send(job_id)
    except Exception as exc:  # noqa: BLE001  (tugas tetap queued; pemulihan mengirim ulang)
        log.warning("gagal mengirim tugas %s ke antrean: %s", job_id, type(exc).__name__)


@router.post("/api/jobs")
async def submit(request: Request, source: str = "csv", target: str = "json",
                 formula_guard: str | None = None, conn=Depends(get_conn),
                 storage: Storage = Depends(get_storage), send=Depends(get_sender)):
    pair = registry.get(source, target)
    if pair is None:
        raise ConversionError(UNSUPPORTED)
    reason = registry.unavailable_reason(pair, tools.detect())
    if reason:
        raise ConversionError(reason, 503)
    limit = limits.max_upload_bytes(pair.source)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _too_large(pair.source, limit)
    await anyio.to_thread.run_sync(_check_capacity, conn, storage)
    try:
        part, size = await storage.write_stream(request.stream(), limit)
    except ClientDisconnect:
        log.info("unggahan terputus sebelum selesai")
        return Response(status_code=400)
    code, code_hash = jobs.new_code()
    job_id = uuid.uuid4()
    options = {"formula_guard": flag(formula_guard)} if "formula_guard" in pair.options else {}
    await anyio.to_thread.run_sync(_store, conn, storage, part, job_id, code_hash, pair, options, size)
    await anyio.to_thread.run_sync(_send, send, job_id)
    return JSONResponse({"kode": code, "status": "queued"}, status_code=202, headers=NO_STORE)


def _lookup(conn, raw: str | None) -> jobs.Job:
    code = jobs.normalize_code(raw)
    if code is None:
        raise ConversionError(INVALID_CODE, 400)
    job = jobs.find_by_code_hash(conn, jobs.hash_code(code))
    conn.commit()
    if job is None:
        raise ConversionError(NOT_FOUND, 404)
    return job


def _is_expired(job: jobs.Job) -> bool:
    if job.status == "expired":
        return True
    return job.status == "success" and job.expires_at is not None and job.expires_at <= jobs.utcnow()


@router.get("/api/jobs/status")
def status(kode: str | None = Header(default=None, alias=CODE_HEADER), conn=Depends(get_conn)):
    job = _lookup(conn, kode)
    expired = _is_expired(job)
    state = "expired" if expired else job.status
    position = jobs.queue_position(conn, job)
    conn.commit()
    return JSONResponse({
        "status": state,
        "source": job.source,
        "target": job.target,
        "dibuat": _iso(job.created_at),
        "selesai": _iso(job.finished_at),
        "kedaluwarsa": _iso(job.expires_at),
        "pesan": EXPIRED if expired else (job.error if job.status == "failed" else None),
        "catatan": list(job.notes) if state == "success" else [],
        "posisi_antrean": position,
    }, headers=NO_STORE)


@router.get("/api/jobs/hasil")
def download(kode: str | None = Header(default=None, alias=CODE_HEADER), conn=Depends(get_conn),
             storage: Storage = Depends(get_storage)):
    job = _lookup(conn, kode)
    if job.status in ("queued", "processing"):
        raise ConversionError(NOT_DONE, 409)
    if job.status == "failed":
        raise ConversionError(job.error or NOT_DONE, 409)
    path = storage.result_path(job.id)
    if _is_expired(job) or not path.is_file():
        raise ConversionError(EXPIRED, 410)
    headers = {**DOWNLOAD_HEADERS,
               "Content-Disposition": f'attachment; filename="hasil.{job.target}"',
               "X-KonversiKu-Notes": json.dumps(list(job.notes), ensure_ascii=True)}
    return FileResponse(path, media_type=registry.MEDIA_TYPES[job.target], headers=headers)
