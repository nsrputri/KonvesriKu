"""Setiap file di contoh/ bisa dikonversi ke semua target aktifnya (uji asap end-to-end API)."""
from pathlib import Path

import pytest

from conftest import convert

CONTOH = Path(__file__).resolve().parents[1] / "contoh"
SAMPLES = {
    "teks.txt": "txt", "catatan.md": "md", "dokumen.rtf": "rtf", "halaman.html": "html",
    "panduan.rst": "rst", "artikel.tex": "tex", "laporan.docx": "docx", "teks.pdf": "pdf",
    "data.csv": "csv", "data.tsv": "tsv", "data.json": "json", "konfigurasi.yaml": "yaml",
    "katalog.xml": "xml",
}


def test_every_source_format_has_a_sample(client):
    assert set(SAMPLES.values()) == set(client.get("/api/formats").json()["labels"])


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_sample_converts_to_all_active_targets(client, name):
    source = SAMPLES[name]
    targets = client.get("/api/formats").json()["pairs"][source]
    if not targets:
        pytest.skip(f"BELUM DIUJI: tidak ada target aktif untuk {source} di lingkungan ini")
    data = (CONTOH / name).read_bytes()
    for target in targets:
        response = convert(client, source, target, data)
        assert response.status_code == 200, (name, target, response.text)
        assert response.content
