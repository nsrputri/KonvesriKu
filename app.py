"""KonversiKu tahap 2: sistem tugas (unggah, kode tugas, antrean, unduh lintas sesi).

Konversi dikerjakan pekerja Celery terpisah (``konversiku/celery_app.py``); API ini hanya
menerima unggahan, mencatat tugas di PostgreSQL, dan melayani status serta hasil.
Endpoint sinkron tahap 1 (``/api/convert``) hanya dipasang bila
``KONVERSIKU_SYNC_ENDPOINT=1`` (untuk tes mesin konversi).
Jalankan: ``python -m uvicorn app:app --host 127.0.0.1 --port 8001`` (dev).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from konversiku import jobs_api, ops_api, registry, tools
from konversiku.common import ConversionError
from konversiku.web import NO_STORE, PAGE_HEADERS, error

log = logging.getLogger("konversiku.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Deteksi Pandoc/LibreOffice sekali saat start, di thread agar event loop tidak tertahan.
    await anyio.to_thread.run_sync(tools.detect)
    yield


app = FastAPI(title="KonversiKu", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.exception_handler(ConversionError)
async def conversion_error(_request: Request, exc: ConversionError):
    return error(exc.status_code, exc.message)


@app.exception_handler(psycopg.Error)
async def database_error(_request: Request, exc: psycopg.Error):
    log.error("PostgreSQL bermasalah: %s", type(exc).__name__)
    return error(503, jobs_api.SERVICE_DOWN)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    return error(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, _exc: RequestValidationError):
    return error(400, "Parameter permintaan tidak valid.")


@app.get("/api/formats")
def formats():
    return JSONResponse(registry.describe(), headers=NO_STORE)


@app.get("/")
def home():
    return FileResponse(Path(__file__).with_name("index.html"), headers=PAGE_HEADERS)


# Ikon situs (folder ikon/). Hanya nama di daftar ini yang disajikan, di root karena browser
# meminta /favicon.ico dan /apple-touch-icon.png langsung; tidak ada folder statis yang dibuka.
ICON_DIR = Path(__file__).with_name("ikon")
ICONS = {
    "favicon.ico": "image/x-icon",
    "favicon-16x16.png": "image/png",
    "favicon-32x32.png": "image/png",
    "apple-touch-icon.png": "image/png",
    "android-chrome-192x192.png": "image/png",
    "android-chrome-512x512.png": "image/png",
    "site.webmanifest": "application/manifest+json",
}
ICON_HEADERS = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"}


def _icon(name: str, media_type: str):
    def serve():
        return FileResponse(ICON_DIR / name, media_type=media_type, headers=ICON_HEADERS)
    return serve


for _name, _type in ICONS.items():
    app.add_api_route(f"/{_name}", _icon(_name, _type), methods=["GET"], include_in_schema=False)


app.include_router(jobs_api.router)
app.include_router(ops_api.router)

if os.environ.get("KONVERSIKU_SYNC_ENDPOINT") == "1":
    from konversiku import sync_api

    app.include_router(sync_api.router)
