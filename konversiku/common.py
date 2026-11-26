"""Tipe hasil, error, dan utilitas bersama."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import limits


class ConversionError(Exception):
    """Kesalahan yang aman ditampilkan ke pengguna (pesan Indonesia)."""

    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class ToolUnavailable(ConversionError):
    status_code = 503


class ConversionTimeout(ConversionError):
    status_code = 504


@dataclass
class Result:
    data: bytes
    notes: list[str] = field(default_factory=list)


def decode_utf8(raw: bytes) -> str:
    try:
        text = bytes(raw).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConversionError("Simpan file teks sebagai UTF-8, lalu coba lagi.") from exc
    if "\x00" in text:
        raise ConversionError("File teks berisi karakter NUL; pastikan ini file teks UTF-8.")
    return text


def check_output(data: bytes) -> bytes:
    if len(data) > limits.MAX_OUTPUT_BYTES:
        raise ConversionError(f"Hasil konversi terlalu besar (maksimal {limits.size_label(limits.MAX_OUTPUT_BYTES)}).")
    return data


def check_text_length(text: str, what: str = "Teks hasil") -> str:
    if len(text) > limits.MAX_TEXT_CHARS:
        raise ConversionError(
            f"{what} terlalu panjang (maksimal {limits.MAX_TEXT_CHARS:,} karakter).".replace(",", "."))
    return text


def _remove_tree(path: str) -> None:
    # Di Windows, proses yang baru dihentikan kadang masih memegang file sesaat.
    for attempt in range(10):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return
        time.sleep(0.1 * (attempt + 1))


@contextmanager
def request_tempdir():
    """Direktori sementara per request; selalu dihapus, sukses maupun gagal."""
    root = os.environ.get("KONVERSIKU_TMPDIR") or None
    path = tempfile.mkdtemp(prefix="konversiku-", dir=root)
    try:
        yield Path(path)
    finally:
        _remove_tree(path)


def format_count(number: int) -> str:
    return f"{number:,}".replace(",", ".")
