"""Header HTTP dan respons error bersama untuk semua router."""
from __future__ import annotations

from fastapi.responses import JSONResponse

NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
DOWNLOAD_HEADERS = {**NO_STORE, "Content-Security-Policy": "default-src 'none'; sandbox",
                    "Referrer-Policy": "no-referrer"}
PAGE_HEADERS = {**NO_STORE, "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


def error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status, headers=NO_STORE)


def flag(value: str | None, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() not in ("0", "false", "off", "no")
