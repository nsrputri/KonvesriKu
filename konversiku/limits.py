"""Batas pemrosesan. Semua angka ini didokumentasikan di PETUNJUK.md.

Nilai dibaca saat dipakai (``limits.NAMA``), sehingga tes dapat menurunkannya.
Batas unggah per format sumber berasal dari pengukuran proyek 4 di VPS (2 inti, RAM 7,8 GiB):
ukuran terbesar yang selesai <= 300 detik dengan beban memori seluruh pohon proses (Python
ditambah Pandoc/LibreOffice) <= 1.500 MiB (sekitar 1,57 GB) untuk target terberat format itu
(laporan_tes/kapasitas_proyek4_verifikasi_pohon.txt). Target 1 GB di PRD belum tercapai.
"""
MiB = 1024 * 1024

# Unggahan per format sumber (kunci: nama kanonik di registry)
MAX_UPLOAD_BYTES_BY_SOURCE = {
    "txt": 2 * MiB,                 # TXT berbaris pendek ke MD/TEX: 3 MiB melewati 1.500 MiB (pohon proses)
    "md": 4 * MiB, "rtf": 4 * MiB, "html": 4 * MiB,   # ke TXT/MD: 5 MiB melewati 1.500 MiB
    "rst": 3 * MiB, "tex": 3 * MiB,                   # ke MD: 4 MiB melewati 1.500 MiB
    "docx": 20 * MiB,               # isi dibatasi MAX_DOCX_XML_BYTES; sisanya gambar
    "pdf": 20 * MiB,                # isi dibatasi MAX_PDF_PAGES dan MAX_TEXT_CHARS
    "csv": 25 * MiB, "tsv": 25 * MiB, "json": 25 * MiB, "yaml": 10 * MiB, "xml": 25 * MiB,
}
MAX_UPLOAD_BYTES = max(MAX_UPLOAD_BYTES_BY_SOURCE.values())   # batas terbesar (Nginx mengikuti)


def size_label(size: int) -> str:
    """Ukuran untuk pesan pengguna: MiB (cara batas dihitung, 1.048.576 byte) lalu MB desimal yang
    lebih dikenal, mis. "25 MiB (26,2 MB)". Sama dengan mib() di index.html."""
    mib = f"{size / MiB:g}".replace(".", ",")
    mb = f"{round(size / 1_000_000, 1):.1f}".removesuffix(".0").replace(".", ",")
    return f"{mib} MiB ({mb} MB)"


def max_upload_bytes(source: str | None) -> int:
    """Batas unggah format sumber; alias memakai format kanoniknya, format tak dikenal batas terkecil."""
    from . import registry  # impor lambat: registry mengimpor modul ini

    known = MAX_UPLOAD_BYTES_BY_SOURCE.get(registry.canonical(source))
    return known if known is not None else min(MAX_UPLOAD_BYTES_BY_SOURCE.values())


MAX_OUTPUT_BYTES = 100 * MiB        # hasil apa pun; CSV 25 MiB ke XML sekitar 65 MiB
MAX_TEXT_CHARS = 5_000_000          # teks hasil ekstraksi DOCX/PDF

# Tabel (CSV/TSV, termasuk hasil perataan data bersarang)
MAX_RECORDS = 500_000               # baris data (tanpa header)
MAX_COLUMNS = 200
MAX_CELLS = 2_000_000               # baris x kolom

# ZIP DOCX diperiksa sebelum dimuat
MAX_ZIP_ENTRIES = 1_000
MAX_ZIP_EXPANDED_BYTES = 64 * MiB   # semua entri setelah dibuka (gambar termasuk)
MAX_DOCX_XML_BYTES = 5 * MiB        # semua bagian berisi XML (isi, gaya, header, catatan); 6 MiB > 1.500 MiB

# JSON/YAML/XML
MAX_NESTING = 64                    # tingkat objek/array/elemen bersarang
MAX_NODES = 2_500_000               # node setelah alias YAML diperluas / elemen XML
MAX_YAML_ALIASES = 50
MAX_YAML_NODES = 1_750_000          # node YAML saat dibaca (kunci termasuk); YAML 10 MiB terukur 1,70 juta
MAX_INT_DIGITS = 4_300              # sama dengan batas bawaan Python

# PDF (dibaca di proses terpisah)
MAX_PDF_PAGES = 1_000
PDF_TIMEOUT = 120                   # detik untuk membaca text layer
PDF_MEMORY_BYTES = 1024 * MiB       # batas memori proses pembaca PDF (Linux, via RLIMIT_AS)

# Proses eksternal dan konkurensi
PANDOC_TIMEOUT = 120                # detik per pemanggilan pandoc (satu tugas bisa 2 kali)
PANDOC_HEAP = "1536M"               # +RTS -M1536M -RTS bila didukung binary pandoc
LIBREOFFICE_TIMEOUT = 360           # detik per konversi DOCX ke PDF (2x kasus terlama 162 s)
MAX_CONCURRENT = 2                  # konversi yang berjalan bersamaan per proses (endpoint sinkron)
MAX_PENDING = 8                     # konversi berjalan + menunggu; lebih dari ini 503


# Tahap 2: antrean tugas, pekerja, retensi (lihat desain tahap 2)
MAX_QUEUED_JOBS = 50                # tugas queued maksimal sebelum submit ditolak 503
DATA_QUOTA_BYTES = 5 * 1000**3      # ukuran folder data maksimal sebelum submit ditolak 503
MAX_ATTEMPTS = 3                    # percobaan per tugas (retry hanya untuk kegagalan di luar file)
TASK_SOFT_TIME_LIMIT = 600          # detik, Celery melempar SoftTimeLimitExceeded
TASK_HARD_TIME_LIMIT = 660          # detik, Celery mematikan proses anak
LEASE_SECONDS = 720                 # lama pegangan tugas processing (batas keras + margin)
BROKER_VISIBILITY_TIMEOUT = 3600    # detik sebelum Redis mengirim ulang pesan yang belum di-ack
RETENTION_HOURS = 24                # hasil/tugas gagal dihapus 24 jam setelah selesai
EXPIRED_ROW_KEEP_DAYS = 7           # baris expired (tanpa isi) disimpan 7 hari lalu dihapus
ORPHAN_AGE_SECONDS = 3600           # .part dan folder tugas tanpa baris dihapus setelah 1 jam
REQUEUE_AFTER_SECONDS = 120         # tugas queued yang belum diambil dikirim ulang setelah 2 menit
WORKER_CONCURRENCY = 2              # proses konversi bersamaan per node pekerja
RETRY_DELAY_SECONDS = 10            # jeda retry = nilai ini x attempts


def public() -> dict:
    """Batas yang ditampilkan ke UI dan /api/formats."""
    return {
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_upload_bytes_per_source": dict(MAX_UPLOAD_BYTES_BY_SOURCE),
        "max_output_bytes": MAX_OUTPUT_BYTES,
        "max_records": MAX_RECORDS,
        "max_columns": MAX_COLUMNS,
        "max_cells": MAX_CELLS,
        "max_zip_entries": MAX_ZIP_ENTRIES,
        "max_zip_expanded_bytes": MAX_ZIP_EXPANDED_BYTES,
        "max_docx_xml_bytes": MAX_DOCX_XML_BYTES,
        "max_nesting": MAX_NESTING,
        "max_nodes": MAX_NODES,
        "max_yaml_aliases": MAX_YAML_ALIASES,
        "max_yaml_nodes": MAX_YAML_NODES,
        "max_pdf_pages": MAX_PDF_PAGES,
        "max_text_chars": MAX_TEXT_CHARS,
        "pandoc_timeout_seconds": PANDOC_TIMEOUT,
        "libreoffice_timeout_seconds": LIBREOFFICE_TIMEOUT,
    }
