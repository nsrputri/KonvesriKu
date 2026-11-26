"""Data: CSV, TSV, JSON, YAML, XML. Setiap pasangan diparse ulang dan dibandingkan isinya."""
import csv
import io
import json

import pytest
import yaml
from defusedxml import ElementTree

from conftest import RECORDS, convert, data_sample, notes_of, records_of

DATA = ["csv", "tsv", "json", "yaml", "xml"]
DATA_PAIRS = [(s, t) for s in DATA for t in DATA if s != t]


def rows(data: bytes, delimiter=","):
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline=""), delimiter=delimiter))


def test_data_pair_count():
    assert len(DATA_PAIRS) == 20


@pytest.mark.parametrize("source, target", DATA_PAIRS, ids=[f"{s}-{t}" for s, t in DATA_PAIRS])
def test_data_pair_preserves_records(client, source, target):
    response = convert(client, source, target, data_sample(source))
    assert response.status_code == 200, response.text
    assert records_of(target, response.content) == RECORDS


# ---------------------------------------------------------- regresi CSV lama

def test_csv_to_json_legacy_quotes_newlines_unicode_leading_zero(client):
    data = 'kode,nama,alamat\r\n001,"Budi, S.T.","Jl. Mawar 1\nKota"\r\n002,Ñandú 日本,"x ""y"""\r\n'.encode()
    response = convert(client, "csv", "json", data)
    assert json.loads(response.content) == [
        {"kode": "001", "nama": "Budi, S.T.", "alamat": "Jl. Mawar 1\nKota"},
        {"kode": "002", "nama": "Ñandú 日本", "alamat": 'x "y"'}]


def test_json_to_csv_legacy_flat_records(client):
    data = json.dumps([{"kode": "001", "n": 2, "ok": True, "x": None},
                       {"kode": "002", "n": 1.5, "ok": False, "x": "Ñ"}]).encode()
    response = convert(client, "json", "csv", data)
    assert rows(response.content) == [["kode", "n", "ok", "x"], ["001", "2", "true", ""],
                                      ["002", "1.5", "false", "Ñ"]]


@pytest.mark.parametrize("payload, status, fragment", [
    (b"a,a\n1,2\n", 400, "header"),
    (b"a,b\n1,2\n3\n", 400, "record 3"),
    (b"a\n", 400, "baris data"),
    ("a\nÿ\n".encode("latin-1"), 400, "UTF-8"),
])
def test_csv_input_validation(client, payload, status, fragment):
    response = convert(client, "csv", "json", payload)
    assert response.status_code == status
    assert fragment in response.json()["detail"]


def test_csv_limits(client, monkeypatch):
    from konversiku import limits

    monkeypatch.setattr(limits, "MAX_RECORDS", 10_000)
    header = ",".join(f"k{i}" for i in range(201))
    too_wide = convert(client, "csv", "json", (header + "\n" + ",".join("1" * 201) + "\n").encode())
    assert too_wide.status_code == 400 and "kolom" in too_wide.json()["detail"]
    too_long = convert(client, "csv", "json", ("a\n" + "x\n" * 10001).encode())
    assert too_long.status_code == 400 and "10.000" in too_long.json()["detail"]


def test_csv_to_yaml_and_xml_keep_text(client):
    data = b"kode,nilai\n001,1e5\n"
    assert yaml.safe_load(convert(client, "csv", "yaml", data).content) == [{"kode": "001", "nilai": "1e5"}]
    root = ElementTree.fromstring(convert(client, "csv", "xml", data).content)
    assert root.tag == "data" and root.find("item/kode").text == "001"


# ------------------------------------------------------- flatten ke CSV/TSV

NESTED = [
    {"id": 1, "user": {"name": "A", "tags": ["x", "y"]}, "meta": None, "kosong": {}, "daftar": []},
    {"id": 2, "user": {"name": "B", "tags": []}, "extra": "baru"},
]


def test_nested_json_flattens_with_documented_paths(client):
    response = convert(client, "json", "csv", json.dumps(NESTED).encode())
    assert response.status_code == 200, response.text
    table = rows(response.content)
    assert table[0] == ["id", "user.name", "user.tags[0]", "user.tags[1]", "meta", "kosong", "daftar",
                        "user.tags", "extra"]
    assert table[1] == ["1", "A", "x", "y", "", "{}", "[]", "", ""]
    assert table[2] == ["2", "B", "", "", "", "", "", "[]", "baru"]
    notes = " ".join(notes_of(response))
    assert "titik" in notes and "[0]" in notes and "null" in notes


def test_nested_yaml_to_tsv(client):
    response = convert(client, "yaml", "tsv", b"- a:\n    b: 1\n    c: [true, null]\n")
    assert rows(response.content, "\t") == [["a.b", "a.c[0]", "a.c[1]"], ["1", "true", ""]]


def test_single_object_becomes_one_row(client):
    response = convert(client, "json", "csv", b'{"nama": "x", "alamat": {"kota": "Sleman"}}')
    assert rows(response.content) == [["nama", "alamat.kota"], ["x", "Sleman"]]


def test_single_key_wrappers_are_unwrapped_and_reported(client):
    payload = json.dumps({"data": {"item": [{"a": "1"}, {"a": "2"}]}}).encode()
    response = convert(client, "json", "csv", payload)
    assert rows(response.content) == [["a"], ["1"], ["2"]]
    assert any("data.item" in note for note in notes_of(response))


@pytest.mark.parametrize("payload, fragment", [
    (b'[{"a.b": 1, "a": {"b": 2}}]', "a.b"),
    (b'[{"t[0]": 1, "t": [2]}]', "t[0]"),
    (b'[1, 2]', "objek"),
    (b'"teks"', "objek"),
    (b'[[1, 2]]', "objek"),
])
def test_flatten_rejects_ambiguous_input_with_specific_message(client, payload, fragment):
    response = convert(client, "json", "csv", payload)
    assert response.status_code == 400, response.text
    assert fragment in response.json()["detail"]


def test_flatten_column_limit(client):
    payload = json.dumps([{"a": list(range(201))}]).encode()
    response = convert(client, "json", "csv", payload)
    assert response.status_code == 400 and "kolom" in response.json()["detail"]


# ------------------------------------------------------- formula injection

RISKY = json.dumps([{"a": "=HYPERLINK(\"http://contoh.test\")", "b": "-5", "c": "@SUM(1)",
                     "d": "+1", "e": "aman"}]).encode()


def test_formula_guard_on_by_default(client):
    response = convert(client, "json", "csv", RISKY)
    assert rows(response.content)[1] == ["'=HYPERLINK(\"http://contoh.test\")", "-5", "'@SUM(1)", "+1", "aman"]
    assert any("2 sel" in note for note in notes_of(response))


def test_formula_guard_off_keeps_data_and_warns(client):
    response = convert(client, "json", "tsv", RISKY, formula_guard="0")
    assert rows(response.content, "\t")[1][0].startswith("=HYPERLINK")
    assert any("formula" in note.lower() for note in notes_of(response))


# ------------------------------------------------------------- XML

def test_xml_convention_attributes_text_lists(client):
    xml = ('<?xml version="1.0"?><!-- komentar hilang --><katalog xmlns:x="urn:x">'
           '<buku id="1" bahasa="id"><judul>  Laskar </judul><x:tag>a</x:tag><kosong/></buku>'
           '<buku id="2"><judul><![CDATA[a < b]]></judul></buku>'
           '<catatan tipe="info">teks bebas</catatan></katalog>').encode()
    response = convert(client, "xml", "json", xml)
    assert response.status_code == 200, response.text
    assert json.loads(response.content) == {"katalog": {
        "@xmlns:x": "urn:x",
        "buku": [{"@id": "1", "@bahasa": "id", "judul": "  Laskar ", "x:tag": "a", "kosong": None},
                 {"@id": "2", "judul": "a < b"}],
        "catatan": {"@tipe": "info", "#text": "teks bebas"},
    }}
    assert any("komentar" in note.lower() for note in notes_of(response))


def test_json_to_xml_rules(client):
    payload = json.dumps({"laporan": {"@versi": 2, "aktif": True, "kosong": None, "list_kosong": [],
                                      "obj_kosong": {}, "matriks": [[1, 2], [3]], "teks": "a < b & c"}})
    response = convert(client, "json", "xml", payload.encode())
    assert response.status_code == 200, response.text
    root = ElementTree.fromstring(response.content)
    assert root.tag == "laporan" and root.get("versi") == "2"
    assert root.find("aktif").text == "true"
    assert root.find("kosong").text is None and root.find("list_kosong").text is None
    assert root.find("obj_kosong").text is None
    assert [m.findall("item") and [i.text for i in m.findall("item")] for m in root.findall("matriks")] == [
        ["1", "2"], ["3"]]
    assert root.find("teks").text == "a < b & c"
    notes = " ".join(notes_of(response)).lower()
    assert "teks" in notes and "kosong" in notes


def test_json_list_root_wrapped_in_data(client):
    root = ElementTree.fromstring(convert(client, "json", "xml", b'[{"a": "1"}, {"a": "2"}]').content)
    assert root.tag == "data" and [i.find("a").text for i in root.findall("item")] == ["1", "2"]


@pytest.mark.parametrize("payload, fragment", [
    (b'[{"nama lengkap": "x"}]', "nama lengkap"),
    (b'[{"1abc": "x"}]', "1abc"),
    (b'{"a": {"@attr": {"x": 1}}}', "@attr"),
    (b'{"a": "kontrol \\u0001"}', "karakter"),
])
def test_json_to_xml_rejects_unrepresentable(client, payload, fragment):
    response = convert(client, "json", "xml", payload)
    assert response.status_code == 400, response.text
    assert fragment in response.json()["detail"]


@pytest.mark.parametrize("payload, fragment", [
    (b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY e "x">]><a>&e;</a>', "DTD"),
    (b'<!DOCTYPE a [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]><a>&lol2;</a>', "DTD"),
    (b'<!DOCTYPE a SYSTEM "http://127.0.0.1:9/x.dtd"><a/>', "DTD"),
    (b'<p>a<b>x</b>c</p>', "campuran"),
    (b'<a><b></a>', "XML tidak valid"),
    (b'<a/><b/>', "XML tidak valid"),
    (b'<a>' * 100 + b'</a>' * 100, "bersarang"),
])
def test_xml_input_validation(client, payload, fragment):
    response = convert(client, "xml", "json", payload)
    assert response.status_code == 400, response.text
    assert fragment in response.json()["detail"]


# ------------------------------------------------------------- JSON / YAML ketat

@pytest.mark.parametrize("payload, fragment", [
    (b'{"a": 1, "a": 2}', "duplikat"),
    (b'{"a": NaN}', "terbatas"),
    (b'{"a": -Infinity}', "terbatas"),
    (b'{"a": 1e400}', "terbatas"),
    (b'{"a": ', "JSON tidak valid"),
    (b"[" * 200 + b"]" * 200, "bersarang"),
    (b'{"a": ' + b"9" * 5000 + b"}", "terlalu panjang"),
])
def test_json_input_validation(client, payload, fragment):
    response = convert(client, "json", "yaml", payload)
    assert response.status_code == 400, response.text
    assert fragment in response.json()["detail"]


def test_yaml_core_schema(client):
    response = convert(client, "yaml", "json", b"a: yes\nb: 2024-01-31\nc: 1:30\nd: 012\ne: '001'\n")
    assert json.loads(response.content) == {"a": "yes", "b": "2024-01-31", "c": "1:30", "d": 12, "e": "001"}


@pytest.mark.parametrize("payload, fragment", [
    (b"a: 1\na: 2\n", "duplikat"),
    (b"a: !!python/object/apply:os.system ['echo x']\n", "tag"),
    (b"a: !!binary aGFsbw==\n", "JSON"),
    (b"a: .nan\n", "terbatas"),
    (b"1: satu\n", "kunci"),
    (b"a: 1\n---\nb: 2\n", "satu dokumen"),
    (b"a: [\n", "YAML tidak valid"),
    (b"a: &a [*a]\n", "rekursif"),
    ((b"[" * 100) + (b"]" * 100), "bersarang"),
])
def test_yaml_input_validation(client, payload, fragment):
    response = convert(client, "yaml", "json", payload)
    assert response.status_code == 400, response.text
    assert fragment in response.json()["detail"]


def test_yaml_alias_bomb_rejected(client):
    lines = ['a: &a ["x","x","x","x","x","x","x","x","x"]']
    for index, name in enumerate("bcdefghi"):
        lines.append(f"{name}: &{name} [" + ",".join([f"*{'abcdefghi'[index]}"] * 9) + "]")
    response = convert(client, "yaml", "json", "\n".join(lines).encode())
    assert response.status_code == 400 and "alias" in response.json()["detail"].lower()


def test_yaml_node_limit_while_loading(client, monkeypatch):
    """YAML padat (banyak nilai kecil) ditolak saat dibaca, sebelum seluruh graf node dibangun:
    PyYAML memakai sekitar 700 byte per node, jadi menunggu pemeriksaan setelah load bisa
    menghabiskan beberapa GB."""
    from konversiku import limits, strictjson

    def too_late(*args, **kwargs):
        raise AssertionError("graf YAML sudah dibangun penuh sebelum batas node diperiksa")

    monkeypatch.setattr(limits, "MAX_YAML_NODES", 100)
    ok = convert(client, "yaml", "json", ("[" + ", ".join("1" * 99) + "]").encode())
    assert ok.status_code == 200, ok.text
    monkeypatch.setattr(strictjson, "validate", too_late)
    response = convert(client, "yaml", "json", ("[" + ", ".join("1" * 100) + "]").encode())
    assert response.status_code == 400
    assert response.json()["detail"] == "YAML terlalu besar (maksimal 100 node)."


def test_yaml_expansion_limit(client):
    lines = ["a0: &a0 [" + ",".join(["1"] * 50) + "]"]
    # 36 alias saja (di bawah batas 50), tetapi ekspansinya 50 x 6^6 node
    lines += [f"a{i}: &a{i} [" + ",".join([f"*a{i - 1}"] * 6) + "]" for i in range(1, 7)]
    response = convert(client, "yaml", "json", "\n".join(lines).encode())
    assert response.status_code == 400 and "terlalu besar" in response.json()["detail"]


def test_yml_alias_and_yaml_output_has_no_aliases(client):
    response = convert(client, "yml", "json", b"base: &b {a: 1}\nlain:\n  <<: *b\n  c: 2\n")
    assert json.loads(response.content)["lain"] == {"a": 1, "c": 2}
    out = convert(client, "json", "yaml", json.dumps({"x": {"a": 1}, "y": {"a": 1}}).encode()).content
    assert b"&" not in out and b"*" not in out
