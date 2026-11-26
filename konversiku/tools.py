"""Deteksi alat sistem opsional (Pandoc, LibreOffice) dan menjalankan subprocess aman.

Kemampuan Pandoc tidak diasumsikan: reader/writer dibaca dari
``--list-input-formats``/``--list-output-formats`` lalu setiap writer yang dipakai
diuji sungguhan dalam mode ``--sandbox``. Pasangan yang butuh reader/writer yang
tidak lolos dinonaktifkan dengan alasan.

Aturan subprocess: argumen berupa list (tanpa shell), format dari allowlist, file
input selalu bernama tetap buatan aplikasi, ada timeout, dan seluruh pohon proses
dihentikan bila timeout.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import limits
from .common import ConversionError, ConversionTimeout

IS_WINDOWS = sys.platform.startswith("win")
PROBE_HTML = b"<p>uji</p>"

# Reader dan writer Pandoc yang dipakai aplikasi (allowlist).
READERS = {"md": "gfm", "rtf": "rtf", "html": "html", "rst": "rst", "tex": "latex", "docx": "docx"}
WRITERS = {"txt": "plain", "md": "gfm", "rtf": "rtf", "html": "html5", "rst": "rst", "tex": "latex",
           "docx": "docx"}
STANDALONE = {"rtf", "latex"}          # perlu -s agar menjadi dokumen utuh


class ToolFailed(ConversionError):
    """Alat eksternal keluar dengan status gagal."""


@dataclass(frozen=True)
class Tools:
    pandoc: str | None = None
    pandoc_version: str | None = None
    pandoc_rts: tuple[str, ...] = ()
    pandoc_readers: frozenset = frozenset()
    pandoc_writers: frozenset = frozenset()
    pandoc_writer_args: dict = field(default_factory=dict)
    libreoffice: str | None = None
    libreoffice_version: str | None = None
    reasons: dict = field(default_factory=dict)


def _disabled() -> set[str]:
    raw = os.environ.get("KONVERSIKU_DISABLE_TOOLS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _creation_kwargs() -> dict:
    if IS_WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def _kill_tree(process: subprocess.Popen) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def run(args: list[str], *, timeout: float, input_bytes: bytes | None = None,
        cwd: str | None = None) -> bytes:
    """Jalankan alat dan kembalikan stdout. Isi dokumen tidak pernah dicatat ke log."""
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise TypeError("argumen subprocess harus list of str")
    process = subprocess.Popen(
        args, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, shell=False, **_creation_kwargs())
    try:
        stdout, stderr = process.communicate(input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        process.communicate()
        raise ConversionTimeout("Konversi melebihi batas waktu dan dihentikan. Coba file yang lebih kecil.")
    except BaseException:
        _kill_tree(process)
        process.communicate()
        raise
    if process.returncode != 0:
        # stderr hanya diperiksa untuk penanda batas memori, tidak pernah dicatat ke log.
        if b"heap" in stderr.lower() or b"memoryerror" in stderr.lower():
            raise ConversionError(
                "Dokumen terlalu besar atau terlalu kompleks untuk dikonversi dalam batas memori server. "
                "Pecah file menjadi beberapa bagian lalu coba lagi.", 413)
        raise ToolFailed("Alat konversi gagal memproses file ini. Periksa apakah isinya valid.")
    return stdout


# ------------------------------------------------------------------ pandoc

def _find_pandoc() -> str | None:
    return os.environ.get("KONVERSIKU_PANDOC") or shutil.which("pandoc")


def _persist(data: bytes, suffix: str) -> str:
    """Simpan data bawaan Pandoc (template/reference) ke file sementara milik proses."""
    handle, path = tempfile.mkstemp(prefix="konversiku-pandoc-", suffix=suffix)
    with os.fdopen(handle, "wb") as file:
        file.write(data)
    atexit.register(lambda: Path(path).unlink(missing_ok=True))
    return path


def _probe_writer(pandoc: str, rts: tuple, writer: str, ast: bytes) -> tuple[str, ...] | None:
    """Kembalikan argumen tambahan yang membuat writer jalan di --sandbox, atau None."""
    base = [pandoc, *rts, "--sandbox", "-f", "json", "-t", writer]
    standalone = ["-s"] if writer in STANDALONE else []
    output = ["-o", "-"] if writer == "docx" else []

    def fallback() -> tuple[str, ...] | None:
        # Build distro tanpa data tertanam: berikan reference/template bawaan sebagai file.
        try:
            if writer == "docx":
                data = run([pandoc, "--print-default-data-file", "reference.docx"], timeout=20)
                return ("--reference-doc", _persist(data, ".docx"))
            if writer in STANDALONE:
                data = run([pandoc, "-D", writer], timeout=20)
                return ("--template", _persist(data, f".{writer}"))
        except (ConversionError, OSError):
            pass
        return None

    extra: tuple[str, ...] | None = ()
    for attempt in range(2):
        if attempt == 1:
            extra = fallback()
            if extra is None:
                return None
        try:
            out = run([*base, *standalone, *extra, *output], timeout=20, input_bytes=ast)
        except (ConversionError, OSError):
            continue
        if out.strip():
            return tuple(standalone) + tuple(extra)
    return None


def _detect_pandoc() -> tuple[dict, str | None]:
    pandoc = _find_pandoc()
    if not pandoc:
        return {}, "Pandoc belum terpasang di server"
    try:
        version_text = run([pandoc, "--version"], timeout=15).decode("utf-8", "replace")
        inputs = set(run([pandoc, "--list-input-formats"], timeout=15).decode().split())
        outputs = set(run([pandoc, "--list-output-formats"], timeout=15).decode().split())
    except (ConversionError, OSError):
        return {}, "Pandoc ditemukan tetapi tidak bisa dijalankan"
    match = re.search(r"pandoc(?:\.exe)?\s+([\d.]+)", version_text)
    version = match.group(1) if match else "?"
    ast = None
    rts: tuple[str, ...] = ()
    for candidate in (("+RTS", f"-M{limits.PANDOC_HEAP}", "-RTS"), ()):
        try:
            ast = run([pandoc, *candidate, "--sandbox", "-f", "html", "-t", "json"],
                      timeout=15, input_bytes=PROBE_HTML)
            json.loads(ast)
            rts = candidate
            break
        except (ConversionError, OSError, ValueError):
            ast = None
    if ast is None:
        return {}, f"Pandoc {version} tidak mendukung --sandbox (butuh versi yang lebih baru)"
    writers, writer_args = set(), {}
    for writer in sorted(set(WRITERS.values()) & outputs):
        extra = _probe_writer(pandoc, rts, writer, ast)
        if extra is not None:
            writers.add(writer)
            writer_args[writer] = extra
    return {"pandoc": pandoc, "pandoc_version": version, "pandoc_rts": rts,
            "pandoc_readers": frozenset(set(READERS.values()) & inputs),
            "pandoc_writers": frozenset(writers), "pandoc_writer_args": writer_args}, None


# ------------------------------------------------------------- libreoffice

WINDOWS_SOFFICE = [
    r"C:\Program Files\LibreOffice\program\soffice.com",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
]


def _find_soffice() -> str | None:
    configured = os.environ.get("KONVERSIKU_SOFFICE")
    if configured:
        return configured
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            if IS_WINDOWS and found.lower().endswith(".exe"):
                console = found[:-4] + ".com"
                if os.path.exists(console):
                    return console
            return found
    if IS_WINDOWS:
        for candidate in WINDOWS_SOFFICE:
            if os.path.exists(candidate):
                return candidate
    return None


def _detect_libreoffice() -> tuple[dict, str | None]:
    soffice = _find_soffice()
    if not soffice:
        return {}, "LibreOffice (soffice) belum terpasang di server"
    try:
        text = run([soffice, "--version"], timeout=60).decode("utf-8", "replace")
    except (ConversionError, OSError):
        return {}, "LibreOffice ditemukan tetapi tidak bisa dijalankan"
    match = re.search(r"LibreOffice\s+([\d.]+)", text)
    return {"libreoffice": soffice, "libreoffice_version": match.group(1) if match else "?"}, None


@lru_cache(maxsize=1)
def detect() -> Tools:
    """Deteksi sekali per proses. Panggil detect.cache_clear() setelah memasang alat."""
    disabled = _disabled()
    values: dict = {}
    reasons: dict = {}
    if "pandoc" in disabled:
        reasons["pandoc"] = "Pandoc dinonaktifkan oleh konfigurasi server"
    else:
        found, reason = _detect_pandoc()
        values.update(found)
        if reason:
            reasons["pandoc"] = reason
    if "libreoffice" in disabled:
        reasons["libreoffice"] = "LibreOffice dinonaktifkan oleh konfigurasi server"
    else:
        found, reason = _detect_libreoffice()
        values.update(found)
        if reason:
            reasons["libreoffice"] = reason
    return Tools(reasons=reasons, **values)


def available(tools: Tools, requirement: str) -> str | None:
    """Alasan bila requirement tidak terpenuhi, None bila tersedia.

    Requirement: ``pandoc-in:<reader>``, ``pandoc-out:<writer>``, atau ``libreoffice``.
    """
    if requirement == "libreoffice":
        return None if tools.libreoffice else tools.reasons.get("libreoffice", "LibreOffice tidak tersedia")
    kind, _, name = requirement.partition(":")
    if not tools.pandoc:
        return tools.reasons.get("pandoc", "Pandoc tidak tersedia")
    if kind == "pandoc-in":
        return None if name in tools.pandoc_readers else f"Pandoc {tools.pandoc_version} tidak punya reader {name}"
    if kind == "pandoc-out":
        return None if name in tools.pandoc_writers else (
            f"Writer Pandoc {name} tidak berjalan dalam mode --sandbox di server ini")
    raise ValueError(requirement)
