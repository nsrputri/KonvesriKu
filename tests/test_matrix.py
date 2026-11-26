"""Matriks tahap 1 sesuai PRD Bagian 6 dan /api/formats yang mengikuti runtime."""
import pytest

from conftest import convert

DOCS = ["txt", "md", "rtf", "html", "rst", "tex"]
DATA = ["csv", "tsv", "json", "yaml", "xml"]


def expected_pairs():
    pairs = set()
    for source in DOCS:
        pairs |= {(source, t) for t in DOCS if t != source}
        pairs |= {(source, "docx"), (source, "pdf")}
    pairs |= {("docx", t) for t in DOCS} | {("docx", "pdf")}
    pairs |= {("pdf", "txt"), ("pdf", "md"), ("pdf", "docx")}
    pairs |= {(s, t) for s in DATA for t in DATA if s != t}
    return pairs


def listed(payload):
    pairs = {(s, t) for s, targets in payload["pairs"].items() for t in targets}
    pairs |= {(s, t) for s, targets in payload["disabled"].items() for t in targets}
    return pairs


def test_matrix_matches_prd_stage_1(client):
    payload = client.get("/api/formats").json()
    assert listed(payload) == expected_pairs()
    assert len(expected_pairs()) == 72


def test_out_of_scope_combinations_are_absent(client):
    pairs = listed(client.get("/api/formats").json())
    for source, target in [("csv", "pdf"), ("json", "docx"), ("md", "csv"), ("docx", "docx"),
                           ("pdf", "pdf"), ("pdf", "html"), ("pdf", "rtf"), ("xml", "txt")]:
        assert (source, target) not in pairs


@pytest.mark.parametrize("source, target", [("csv", "json"), ("json", "csv"),
                                            ("docx", "txt"), ("txt", "docx")])
def test_legacy_pairs_need_no_system_tool(client, monkeypatch, source, target):
    from konversiku import tools

    monkeypatch.setenv("KONVERSIKU_DISABLE_TOOLS", "pandoc,libreoffice")
    tools.detect.cache_clear()
    try:
        assert target in client.get("/api/formats").json()["pairs"][source]
    finally:
        monkeypatch.delenv("KONVERSIKU_DISABLE_TOOLS")
        tools.detect.cache_clear()


def test_aliases(client):
    assert client.get("/api/formats").json()["aliases"] == {"markdown": "md", "htm": "html", "yml": "yaml"}


def test_formats_not_cached_and_publish_limits(client):
    response = client.get("/api/formats")
    assert response.headers["cache-control"] == "no-store"
    published = response.json()["limits"]
    assert published["max_upload_bytes"] == max(published["max_upload_bytes_per_source"].values())
    assert set(published["max_upload_bytes_per_source"]) == set(response.json()["pairs"])


def test_every_listed_pair_has_notes(client):
    payload = client.get("/api/formats").json()
    for source, target in listed(payload):
        assert isinstance(payload["notes"][source][target], list)


def test_unknown_pair_rejected_with_clear_message(client):
    response = convert(client, "csv", "pdf", b"a\n1\n")
    assert response.status_code == 400
    assert "tidak didukung" in response.json()["detail"]


@pytest.fixture()
def no_tools(monkeypatch):
    from konversiku import tools

    monkeypatch.setenv("KONVERSIKU_DISABLE_TOOLS", "pandoc,libreoffice")
    tools.detect.cache_clear()
    yield
    monkeypatch.delenv("KONVERSIKU_DISABLE_TOOLS")
    tools.detect.cache_clear()


def test_runtime_disables_pairs_with_reason(client, no_tools):
    payload = client.get("/api/formats").json()
    assert payload["pairs"]["txt"] == ["html", "docx"]
    assert payload["pairs"]["docx"] == ["txt"]
    assert payload["pairs"]["pdf"] == ["txt", "docx"]
    assert payload["pairs"]["rtf"] == []
    assert "Pandoc" in payload["disabled"]["md"]["html"]
    assert "LibreOffice" in payload["disabled"]["docx"]["pdf"]
    reason = payload["disabled"]["md"]["pdf"]
    assert "Pandoc" in reason and "LibreOffice" in reason
    assert sorted(payload["pairs"]["json"]) == ["csv", "tsv", "xml", "yaml"]
    active = sum(len(v) for v in payload["pairs"].values())
    assert active == 25


def test_disabled_pair_returns_503_with_reason(client, no_tools):
    response = convert(client, "md", "html", b"# Judul\n")
    assert response.status_code == 503
    assert "Pandoc" in response.json()["detail"]
