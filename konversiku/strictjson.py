"""JSON ketat dan validasi model data yang kompatibel dengan JSON."""
from __future__ import annotations

import json
import math
from typing import Any

from . import limits
from .common import ConversionError, format_count

SCALARS = (str, int, float, bool, type(None))


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ConversionError(f"Kunci JSON tidak boleh duplikat: {key!r}.")
        result[key] = value
    return result


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ConversionError("Angka JSON harus terbatas (NaN, Infinity, dan overflow ditolak).")
    return value


def _bounded_int(text: str) -> int:
    if len(text.lstrip("-")) > limits.MAX_INT_DIGITS:
        raise ConversionError("Angka JSON terlalu panjang.")
    return int(text)


def _constant(name: str):
    raise ConversionError("Angka JSON harus terbatas (NaN, Infinity, dan overflow ditolak).")


def loads(text: str, check_structure: bool = True) -> Any:
    """json.loads tanpa duplicate key, NaN, Infinity, overflow, atau nesting berlebihan.

    ``check_structure=False`` dipakai pembaca tabel, yang punya batas baris/kolom/sel
    sendiri dan menolak nilai bersarang.
    """
    try:
        data = json.loads(text, object_pairs_hook=_pairs, parse_float=_finite_float,
                          parse_int=_bounded_int, parse_constant=_constant)
    except json.JSONDecodeError as exc:
        raise ConversionError(
            f"JSON tidak valid: {exc.msg} (baris {exc.lineno}, kolom {exc.colno}).") from exc
    except RecursionError as exc:
        raise ConversionError(_nesting_message()) from exc
    if check_structure:
        validate(data, "JSON")
    return data


def _nesting_message() -> str:
    return f"Struktur terlalu dalam bersarang (maksimal {limits.MAX_NESTING} tingkat)."


def _path(parts: tuple) -> str:
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out or "(akar)"


def validate(data: Any, origin: str) -> None:
    """Pastikan data hanya berisi tipe JSON, angka terbatas, kunci teks, dan ukuran wajar.

    Referensi bersama (alias YAML) dihitung setiap kali dikunjungi, sehingga
    ekspansi alias yang meledak tetap terdeteksi. Struktur rekursif ditolak.
    """
    count = 0
    active: set[int] = set()

    def visit(value: Any, depth: int, parts: tuple) -> None:
        nonlocal count
        count += 1
        if count > limits.MAX_NODES:
            raise ConversionError(
                "Struktur data terlalu besar setelah alias diperluas "
                f"(maksimal {format_count(limits.MAX_NODES)} node).")
        if depth > limits.MAX_NESTING:
            raise ConversionError(_nesting_message())
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return
        if isinstance(value, int):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ConversionError(
                    f"Angka di '{_path(parts)}' harus terbatas; NaN/Infinity tidak didukung JSON.")
            return
        if isinstance(value, (dict, list)):
            marker = id(value)
            if marker in active:
                raise ConversionError(f"{origin} memakai alias rekursif yang tidak bisa diubah ke JSON.")
            active.add(marker)
            try:
                if isinstance(value, dict):
                    for key, item in value.items():
                        if not isinstance(key, str):
                            raise ConversionError(
                                f"Setiap kunci {origin} harus berupa teks; kunci {key!r} di "
                                f"'{_path(parts)}' bukan teks. Beri tanda kutip.")
                        visit(item, depth + 1, parts + (key,))
                else:
                    for index, item in enumerate(value):
                        visit(item, depth + 1, parts + (index,))
            finally:
                active.discard(marker)
            return
        raise ConversionError(
            f"Nilai bertipe {type(value).__name__} di '{_path(parts)}' tidak kompatibel dengan JSON.")

    visit(data, 0, ())


def dumps(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


path_of = _path
