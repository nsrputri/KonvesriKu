"""Data tahap 1: CSV, TSV, JSON, YAML, XML (konversi antaranggota).

Model bersama adalah nilai yang kompatibel dengan JSON (objek, list, teks, angka,
boolean, null).

Aturan perataan ke CSV/TSV (tidak ada data yang dibuang diam-diam):
* Baris: array objek -> satu baris per objek; satu objek -> satu baris. Bila data
  adalah objek satu-kunci yang (berulang) berujung pada list, list itu dipakai
  sebagai baris dan jalurnya dilaporkan (contoh XML ``data.item``).
* Kolom: objek bersarang memakai titik (``user.name``), list memakai indeks
  (``tags[0]``). Objek/list kosong ditulis ``{}`` / ``[]``. null dan kunci yang tidak
  ada di suatu baris menjadi sel kosong (dilaporkan). Boolean ``true``/``false``.
* Nama kolom yang bentrok (mis. kunci ``a.b`` dan jalur ``a`` -> ``b``) ditolak
  dengan nama kolomnya. Elemen array yang bukan objek ditolak.
* CSV/TSV sebagai sumber: semua nilai tetap teks (001 tetap 001); header bertitik
  tidak dibuka kembali menjadi objek.

Konvensi XML (dibaca dengan xmltodict setelah lolos pemeriksaan defusedxml):
* Elemen -> kunci; atribut -> ``@nama``; teks elemen yang juga punya atribut ->
  ``#text``; elemen berulang -> list; elemen kosong -> null; semua nilai teks.
* DTD/entitas ditolak (XXE, billion laughs). Konten campuran ditolak. Komentar dan
  processing instruction tidak dibawa. Satu elemen dibaca sebagai objek, beberapa
  elemen sebagai list, jadi round-trip tidak dijamin sempurna.
* Menulis XML: objek satu-kunci menjadi root; selain itu dibungkus ``<data>``;
  item list tingkat atas menjadi ``<item>``; list di dalam list menjadi ``<item>``;
  null, ``[]``, ``{}`` menjadi elemen kosong; nilai ditulis sebagai teks.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re

import xmltodict
import yaml
from defusedxml import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden
from defusedxml import ElementTree as SafeET

from . import limits, strictjson
from .common import ConversionError, Result, check_output, decode_utf8, format_count

LABEL = {",": "CSV", "\t": "TSV"}
NUMBER = re.compile(r"[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?")
RISKY_START = ("=", "+", "-", "@", "\t", "\r")
MERGE_TAG = "tag:yaml.org,2002:merge"
_START = ("A-Za-z_À-ÖØ-öø-˿Ͱ-ͽͿ-῿"
          "‌-‍⁰-↏Ⰰ-⿯、-퟿豈-﷏ﷰ-�")
_CHAR = _START + r"0-9.\-·̀-ͯ‿-⁀"
NCNAME = f"[{_START}][{_CHAR}]*"
XML_NAME = re.compile(f"^(?:{NCNAME}:)?{NCNAME}$")
XML_BAD_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


# ------------------------------------------------------------------ CSV/TSV

def read_delimited(raw: bytes, delimiter: str) -> list[dict]:
    label = LABEL[delimiter]
    text = decode_utf8(raw)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        header = next(reader, None) or []
        if len(header) > limits.MAX_COLUMNS:
            raise ConversionError(f"{label} memiliki {len(header)} kolom; maksimal {limits.MAX_COLUMNS} kolom.")
        if not header or any(not h.strip() for h in header) or len(set(header)) != len(header):
            raise ConversionError(f"{label} wajib memiliki header unik dan tidak kosong.")
        records = []
        for line, row in enumerate(reader, 2):
            if not row:
                continue
            if len(row) != len(header):
                raise ConversionError(f"Jumlah kolom tidak sesuai pada record {line}.")
            if len(records) >= limits.MAX_RECORDS:
                raise ConversionError(f"Batas {format_count(limits.MAX_RECORDS)} baris data.")
            records.append(dict(zip(header, row)))
            if len(records) * len(header) > limits.MAX_CELLS:
                raise ConversionError(f"Tabel terlalu besar (maksimal {format_count(limits.MAX_CELLS)} sel).")
    except csv.Error as exc:
        raise ConversionError(f"{label} tidak valid: {exc}.") from exc
    if not records:
        raise ConversionError(f"{label} belum berisi baris data.")
    return records


def _scalar_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def is_risky(text: str) -> bool:
    return bool(text) and text.startswith(RISKY_START) and not NUMBER.fullmatch(text)


def table_rows(model) -> tuple[list[dict], str | None]:
    value, path = model, []
    while isinstance(value, dict) and len(value) == 1:
        key, inner = next(iter(value.items()))
        if isinstance(inner, list):
            path.append(key)
            value = inner
            break
        if isinstance(inner, dict):
            path.append(key)
            value = inner
            continue
        break
    if not isinstance(value, list):
        value, path = model, []          # tidak berujung pada list: data utuh = satu baris
    if isinstance(value, dict):
        return [value], None
    if not isinstance(value, list):
        raise ConversionError("Untuk CSV/TSV, data harus berupa array objek atau satu objek.")
    if not value:
        raise ConversionError("Data tidak berisi baris untuk ditulis ke CSV/TSV.")
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ConversionError(
                f"Elemen ke-{index} bukan objek; CSV/TSV butuh array objek (satu objek per baris).")
    return value, ".".join(path) or None


def flatten_row(row: dict, number: int) -> dict[str, str | None]:
    out: dict[str, str | None] = {}

    def put(name: str, value):
        if name in out:
            raise ConversionError(
                f"Kolom hasil '{name}' muncul dua kali pada baris {number}: ada kunci yang mengandung titik "
                "atau [indeks] yang bentrok dengan jalur bersarang. Ganti nama kunci itu.")
        out[name] = value

    def walk(value, name: str):
        if isinstance(value, dict):
            if not value:
                put(name, "{}")
            for key, item in value.items():
                walk(item, f"{name}.{key}")
        elif isinstance(value, list):
            if not value:
                put(name, "[]")
            for index, item in enumerate(value):
                walk(item, f"{name}[{index}]")
        else:
            put(name, None if value is None else _scalar_text(value))

    for key, value in row.items():
        walk(value, key)
    return out


def write_delimited(model, delimiter: str, guard: bool) -> Result:
    rows, path = table_rows(model)
    if len(rows) > limits.MAX_RECORDS:
        raise ConversionError(f"Batas {format_count(limits.MAX_RECORDS)} baris data.")
    flat = [flatten_row(row, number) for number, row in enumerate(rows, 1)]
    columns: dict[str, None] = {}
    for record in flat:
        for name in record:
            columns.setdefault(name, None)
            if len(columns) > limits.MAX_COLUMNS:
                raise ConversionError(
                    f"Hasil perataan memiliki lebih dari {limits.MAX_COLUMNS} kolom; maksimal "
                    f"{limits.MAX_COLUMNS} kolom.")
    header = list(columns)
    if len(header) * len(flat) > limits.MAX_CELLS:
        raise ConversionError(f"Tabel terlalu besar (maksimal {format_count(limits.MAX_CELLS)} sel).")
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=delimiter)
    risky = nulls = missing = 0

    def guarded(text: str) -> str:
        nonlocal risky
        if is_risky(text):
            risky += 1
            return "'" + text if guard else text
        return text

    writer.writerow([guarded(name) for name in header])
    for record in flat:
        cells = []
        for name in header:
            if name not in record:
                missing += 1
                cells.append("")
            elif record[name] is None:
                nulls += 1
                cells.append("")
            else:
                cells.append(guarded(record[name]))
        writer.writerow(cells)
    notes = []
    if path:
        notes.append(f"Baris diambil dari jalur {path}.")
    if nulls:
        notes.append(f"{nulls} nilai null ditulis sebagai sel kosong.")
    if missing:
        notes.append(f"{missing} sel kosong karena kuncinya tidak ada di baris itu.")
    if risky and guard:
        notes.append(f"{risky} sel diawali =, +, -, atau @ diberi awalan ' agar tidak dijalankan "
                     "sebagai formula oleh spreadsheet.")
    elif risky:
        notes.append(f"{risky} sel diawali =, +, -, atau @ dibiarkan apa adanya; spreadsheet bisa "
                     "menjalankannya sebagai formula. Buka lewat impor teks.")
    return Result(check_output(output.getvalue().encode("utf-8")), notes)


# --------------------------------------------------------------------- YAML

class _CoreSchemaLoader(yaml.SafeLoader):
    """SafeLoader dengan resolver YAML 1.2 core dan batas alias/kedalaman."""

    def __init__(self, stream):
        super().__init__(stream)
        self._aliases = 0
        self._depth = 0
        self._nodes = 0

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            self._aliases += 1
            if self._aliases > limits.MAX_YAML_ALIASES:
                raise ConversionError(f"YAML memakai terlalu banyak alias (maksimal {limits.MAX_YAML_ALIASES}).")
        else:
            # Dihitung saat dibaca: PyYAML memakai sekitar 700 byte per node, jadi YAML padat
            # harus berhenti sebelum seluruh graf dibangun (alias dihitung lagi oleh strictjson).
            self._nodes += 1
            if self._nodes > limits.MAX_YAML_NODES:
                raise ConversionError(f"YAML terlalu besar (maksimal {format_count(limits.MAX_YAML_NODES)} node).")
        self._depth += 1
        if self._depth > limits.MAX_NESTING:
            raise ConversionError(f"Struktur terlalu dalam bersarang (maksimal {limits.MAX_NESTING} tingkat).")
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key_node, _value in node.value:
                if key_node.tag == MERGE_TAG:
                    continue
                key = self.construct_object(key_node, deep=True)
                if not isinstance(key, str):
                    raise ConversionError(
                        f"Setiap kunci YAML harus berupa teks; kunci {key!r} (baris "
                        f"{key_node.start_mark.line + 1}) bukan teks. Beri tanda kutip.")
                if key in seen:
                    raise ConversionError(f"Kunci YAML duplikat: {key!r} (baris {key_node.start_mark.line + 1}).")
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _core_int(loader, node):
    value = loader.construct_scalar(node)
    sign = -1 if value.startswith("-") else 1
    digits = value.lstrip("+-")
    if len(digits) > limits.MAX_INT_DIGITS:
        raise ConversionError("Angka YAML terlalu panjang.")
    if digits.startswith("0o"):
        return sign * int(digits[2:], 8)
    if digits.startswith("0x"):
        return sign * int(digits[2:], 16)
    return sign * int(digits, 10)


def _core_float(loader, node):
    value = loader.construct_scalar(node).lower()
    if value in (".nan", "+.nan", "-.nan") or value.lstrip("+-") == ".inf":
        raise ConversionError("Angka YAML harus terbatas; .nan dan .inf tidak didukung JSON.")
    number = float(value)
    if not math.isfinite(number):
        raise ConversionError("Angka YAML harus terbatas (overflow ditolak).")
    return number


def _install_core_schema() -> None:
    replaced = {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:int",
                "tag:yaml.org,2002:float", "tag:yaml.org,2002:timestamp"}
    _CoreSchemaLoader.yaml_implicit_resolvers = {
        first: [(tag, regexp) for tag, regexp in resolvers if tag not in replaced]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    _CoreSchemaLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
    _CoreSchemaLoader.add_implicit_resolver(
        "tag:yaml.org,2002:int", re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"),
        list("-+0123456789"))
    _CoreSchemaLoader.add_implicit_resolver(
        "tag:yaml.org,2002:float",
        re.compile(r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
                   r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"),
        list("-+.0123456789"))
    _CoreSchemaLoader.add_constructor("tag:yaml.org,2002:int", _core_int)
    _CoreSchemaLoader.add_constructor("tag:yaml.org,2002:float", _core_float)


_install_core_schema()


def read_yaml(raw: bytes):
    text = decode_utf8(raw)
    try:
        data = yaml.load(text, Loader=_CoreSchemaLoader)  # noqa: S506 (turunan SafeLoader)
    except yaml.constructor.ConstructorError as exc:
        tag = re.search(r"tag '([^']+)'", str(exc))
        raise ConversionError(f"YAML memakai tag yang tidak didukung{': ' + tag.group(1) if tag else ''}.") from exc
    except yaml.composer.ComposerError as exc:
        if "expected a single document" in str(exc):
            raise ConversionError("YAML harus berisi satu dokumen saja (tanpa pemisah ---).") from exc
        raise ConversionError(f"YAML tidak valid: {exc.problem}.") from exc
    except yaml.MarkedYAMLError as exc:
        line = f" (baris {exc.problem_mark.line + 1})" if exc.problem_mark else ""
        raise ConversionError(f"YAML tidak valid: {exc.problem}{line}.") from exc
    except yaml.YAMLError as exc:
        raise ConversionError("YAML tidak valid.") from exc
    except RecursionError as exc:
        raise ConversionError(f"Struktur terlalu dalam bersarang (maksimal {limits.MAX_NESTING} tingkat).") from exc
    if data is None:
        raise ConversionError("YAML kosong; tidak ada data untuk dikonversi.")
    strictjson.validate(data, "YAML")
    return data


class _NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def write_yaml(model) -> bytes:
    return yaml.dump(model, Dumper=_NoAliasDumper, allow_unicode=True, sort_keys=False,
                     default_flow_style=False, width=4096).encode("utf-8")


# ---------------------------------------------------------------------- XML

def _check_xml_tree(root) -> None:
    count = 0
    stack = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        count += 1
        if count > limits.MAX_NODES:
            raise ConversionError(f"XML terlalu besar (maksimal {format_count(limits.MAX_NODES)} elemen).")
        if depth > limits.MAX_NESTING:
            raise ConversionError(f"Struktur XML terlalu dalam bersarang (maksimal {limits.MAX_NESTING} tingkat).")
        children = list(element)
        if children and ((element.text or "").strip() or any((c.tail or "").strip() for c in children)):
            raise ConversionError(
                f"XML berisi konten campuran (teks di antara elemen) di <{element.tag}> yang tidak bisa "
                "direpresentasikan sebagai data tanpa mengubah urutan. Gunakan XML data biasa.")
        stack.extend((child, depth + 1) for child in children)


def _drop_layout_whitespace(value):
    if isinstance(value, dict):
        has_children = any(not k.startswith("@") and k != "#text" for k in value)
        out = {}
        for key, item in value.items():
            if key == "#text" and has_children and isinstance(item, str) and not item.strip():
                continue
            out[key] = _drop_layout_whitespace(item)
        return out
    if isinstance(value, list):
        return [_drop_layout_whitespace(item) for item in value]
    return value


def read_xml(raw: bytes):
    data = bytes(raw)
    try:
        root = SafeET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden) as exc:
        raise ConversionError("XML dengan DTD atau entitas tidak diizinkan (mencegah XXE dan entity expansion).") from exc
    except SafeET.ParseError as exc:
        raise ConversionError(f"XML tidak valid: {exc}.") from exc
    _check_xml_tree(root)
    try:
        model = xmltodict.parse(data, strip_whitespace=False, disable_entities=True)
    except Exception as exc:  # expat/xmltodict
        raise ConversionError(f"XML tidak valid: {exc}.") from exc
    model = _drop_layout_whitespace(model)
    strictjson.validate(model, "XML")
    return model


def _xml_text(value, where: str) -> str:
    text = _scalar_text(value)
    if XML_BAD_CHARS.search(text):
        raise ConversionError(f"Teks di '{where}' berisi karakter kontrol yang tidak boleh ada di XML.")
    return text


def _check_name(name: str, where: str) -> None:
    if not XML_NAME.match(name) or XML_BAD_CHARS.search(name):
        raise ConversionError(
            f"Kunci '{name}' di '{where}' bukan nama elemen XML yang sah (harus diawali huruf atau _, "
            "tanpa spasi). Ganti nama kunci atau header itu.")


def _prepare_value(value, where: str):
    if isinstance(value, list):
        if not value:
            return None
        return [{"item": _prepare_value(item, f"{where}[{i}]")} if isinstance(item, list)
                else _prepare_value(item, f"{where}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        if not value:
            return None
        return _prepare_object(value, where)
    if value is None:
        return None
    return _xml_text(value, where)


def _prepare_object(obj: dict, where: str) -> dict:
    out = {}
    has_children = any(not k.startswith("@") and k != "#text" for k in obj)
    for key, value in obj.items():
        path = f"{where}.{key}" if where else key
        if key.startswith("@"):
            _check_name(key[1:], where or "(akar)")
            if isinstance(value, (dict, list)):
                raise ConversionError(f"Atribut '{key}' di '{where or '(akar)'}' harus bernilai skalar.")
            out[key] = "" if value is None else _xml_text(value, path)
        elif key == "#text":
            if isinstance(value, (dict, list)) or has_children:
                raise ConversionError(
                    f"'#text' di '{where or '(akar)'}' harus skalar dan tidak boleh digabung dengan elemen "
                    "anak (konten campuran tidak didukung).")
            out[key] = "" if value is None else _xml_text(value, path)
        else:
            _check_name(key, where or "(akar)")
            out[key] = _prepare_value(value, path)
    return out


def write_xml(model) -> bytes:
    if isinstance(model, dict) and len(model) == 1:
        key, value = next(iter(model.items()))
        root = model if not key.startswith(("@", "#")) and not isinstance(value, list) else {"data": model}
    elif isinstance(model, list):
        root = {"data": {"item": model}}
    else:
        root = {"data": model}
    prepared = _prepare_object(root, "")
    text = xmltodict.unparse(prepared, pretty=True, indent="  ", full_document=True, encoding="utf-8")
    output = text.encode("utf-8")
    try:
        SafeET.fromstring(output, forbid_dtd=True)
    except SafeET.ParseError as exc:
        raise ConversionError(
            f"Data tidak bisa ditulis sebagai XML yang valid ({exc}). Periksa prefix namespace (@xmlns:...).") from exc
    return output


# ---------------------------------------------------------------- pasangan

READERS = {
    "csv": lambda raw: read_delimited(raw, ","),
    "tsv": lambda raw: read_delimited(raw, "\t"),
    "json": lambda raw: strictjson.loads(decode_utf8(raw)),
    "yaml": read_yaml,
    "xml": read_xml,
}


def converter(source: str, target: str):
    def convert(raw: bytes, formula_guard: bool = True) -> Result:
        model = READERS[source](raw)
        if target == "csv":
            return write_delimited(model, ",", formula_guard)
        if target == "tsv":
            return write_delimited(model, "\t", formula_guard)
        if target == "json":
            return Result(check_output(strictjson.dumps(model)))
        if target == "yaml":
            return Result(check_output(write_yaml(model)))
        return Result(check_output(write_xml(model)))
    return convert
