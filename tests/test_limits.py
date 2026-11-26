"""Batas proyek 4: batas unggah per format sumber dan keseimbangan batas pendamping."""
from __future__ import annotations

from konversiku import limits, registry

DATA = ("csv", "tsv", "json", "yaml", "xml")


def test_every_source_has_a_limit():
    for source in registry.SOURCE_ORDER:
        assert source in limits.MAX_UPLOAD_BYTES_BY_SOURCE, source
        assert 0 < limits.max_upload_bytes(source) <= limits.MAX_UPLOAD_BYTES
    assert limits.MAX_UPLOAD_BYTES == max(limits.MAX_UPLOAD_BYTES_BY_SOURCE.values())


def test_limits_raised_beyond_tahap1():
    assert limits.MAX_UPLOAD_BYTES > 2 * limits.MiB


def test_alias_uses_canonical_limit():
    assert limits.max_upload_bytes("yml") == limits.max_upload_bytes("yaml")
    assert limits.max_upload_bytes("markdown") == limits.max_upload_bytes("md")
    assert limits.max_upload_bytes("HTM") == limits.max_upload_bytes("html")


def test_unknown_source_gets_smallest_limit():
    assert limits.max_upload_bytes("exe") == min(limits.MAX_UPLOAD_BYTES_BY_SOURCE.values())


def test_tool_timeouts_fit_task_limit():
    assert 2 * limits.PANDOC_TIMEOUT + limits.LIBREOFFICE_TIMEOUT <= limits.TASK_SOFT_TIME_LIMIT
    assert limits.PDF_TIMEOUT <= limits.TASK_SOFT_TIME_LIMIT
    assert (limits.TASK_SOFT_TIME_LIMIT, limits.TASK_HARD_TIME_LIMIT, limits.LEASE_SECONDS) == (600, 660, 720)
    assert limits.PANDOC_HEAP == "1536M"


def test_tool_timeouts_keep_double_margin_over_measured():
    """Cadangan 2x (desain proyek 4) terhadap tahap terlama yang terukur di VPS
    (laporan_tes/kapasitas_proyek4_verifikasi.txt): LibreOffice 161,9 s (TXT 3 MiB berbaris
    pendek ke PDF); setiap pasangan lewat Pandoc selesai <= 36 s termasuk LibreOffice."""
    assert limits.LIBREOFFICE_TIMEOUT >= 2 * 162
    assert limits.PANDOC_TIMEOUT >= 2 * 36


def test_output_limit_covers_largest_data_input():
    biggest = max(limits.max_upload_bytes(source) for source in DATA)
    assert limits.MAX_OUTPUT_BYTES >= 4 * biggest


def test_public_limits_list_every_source():
    public = limits.public()
    per_source = public["max_upload_bytes_per_source"]
    assert set(per_source) == set(registry.SOURCE_ORDER)
    assert public["max_upload_bytes"] == max(per_source.values())


def test_size_label_shows_mib_and_mb():
    """Batas dihitung biner (MiB, 1.048.576 byte); MB desimal disertakan karena lebih dikenal."""
    assert limits.size_label(25 * limits.MiB) == "25 MiB (26,2 MB)"
    assert limits.size_label(20 * limits.MiB) == "20 MiB (21 MB)"
    assert limits.size_label(2 * limits.MiB) == "2 MiB (2,1 MB)"
    assert limits.size_label(100 * limits.MiB) == "100 MiB (104,9 MB)"


def test_public_lists_content_limits():
    public = limits.public()
    assert public["max_docx_xml_bytes"] == limits.MAX_DOCX_XML_BYTES
    assert public["max_pdf_pages"] == limits.MAX_PDF_PAGES
    assert public["max_text_chars"] == limits.MAX_TEXT_CHARS
    assert limits.MAX_DOCX_XML_BYTES <= limits.MAX_ZIP_EXPANDED_BYTES
