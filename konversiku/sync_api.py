"""Endpoint sinkron tahap 1 (``POST /api/convert``), hanya untuk tes mesin konversi.

Tidak dipasang di aplikasi produksi tahap 2: ``app.py`` menyertakannya hanya bila
``KONVERSIKU_SYNC_ENDPOINT=1`` (diset oleh ``tests/conftest.py`` dan ``ukur_kapasitas.py``).
"""
from __future__ import annotations

import json

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import limits, registry, tools
from .common import ConversionError, Result
from .web import DOWNLOAD_HEADERS, flag

router = APIRouter()

_limiter = anyio.CapacityLimiter(limits.MAX_CONCURRENT)
_pending = 0


async def read_body(request: Request, source: str) -> bytes:
    """Baca body secara stream dan berhenti begitu melewati batas format sumber."""
    limit = limits.max_upload_bytes(source)
    too_large = f"File {source.upper()} melebihi batas {limits.size_label(limit)}."
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ConversionError(too_large, 413)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise ConversionError(too_large, 413)
    if not raw:
        raise ConversionError("File kosong. Pilih file berisi data.")
    return bytes(raw)


def _busy() -> ConversionError:
    return ConversionError("Server sedang sibuk. Coba lagi sebentar lagi.", 503)


async def run_limited(function):
    """Jalankan converter di thread dengan batas konkurensi; tolak bila terlalu ramai."""
    global _pending
    if _pending >= limits.MAX_PENDING:
        raise _busy()
    _pending += 1
    try:
        return await anyio.to_thread.run_sync(function, limiter=_limiter)
    finally:
        _pending -= 1


@router.post("/api/convert")
async def convert(request: Request, source: str = "csv", target: str = "json",
                  formula_guard: str | None = None):
    pair = registry.get(source, target)
    if pair is None:
        raise ConversionError("Pasangan format tidak didukung. Lihat daftar di Bantuan atau /api/formats.")
    reason = registry.unavailable_reason(pair, tools.detect())
    if reason:
        raise ConversionError(reason, 503)
    if _pending >= limits.MAX_PENDING:
        raise _busy()
    raw = await read_body(request, pair.source)
    kwargs = {"formula_guard": flag(formula_guard)} if "formula_guard" in pair.options else {}
    result: Result = await run_limited(lambda: pair.run(raw, **kwargs))
    notes = [*pair.notes, *result.notes]
    headers = {**DOWNLOAD_HEADERS,
               "Content-Disposition": f'attachment; filename="hasil.{pair.target}"',
               "X-KonversiKu-Notes": json.dumps(notes, ensure_ascii=True)}
    return Response(result.data, media_type=registry.MEDIA_TYPES[pair.target], headers=headers)
