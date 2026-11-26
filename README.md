# KonversiKu

Layanan konversi file berbasis teks di sisi server, untuk proyek gabungan Komputasi Awan
dan praktikum AMJ. File diunggah, pengguna langsung mendapat **kode tugas**, konversi
dikerjakan **pekerja terpisah lewat antrean**, dan hasilnya bisa diunduh dari **sesi atau
perangkat lain** dengan kode itu. File asli dihapus begitu konversi selesai, dan hasil
dihapus otomatis 24 jam setelah selesai.

## Format dan batas ukuran

72 pasangan konversi. Batas dihitung biner (1 MiB = 1.048.576 byte) dan ditentukan dari
pengukuran di VPS: file sebesar batas selesai dalam 300 detik dengan memori maksimal
1.500 MiB per tugas.

| File asal | Bisa jadi | Maks. file | Dikonversi pakai |
| --- | --- | --- | --- |
| TXT | MD, RTF, HTML, RST, TEX, DOCX, PDF | 2 MiB (2,1 MB) | Python, python-docx, Pandoc; PDF lewat LibreOffice |
| MD, RTF, HTML | 6 format dokumen lain, DOCX, PDF | 4 MiB (4,2 MB) | Pandoc; PDF: Pandoc ke DOCX lalu LibreOffice |
| RST, TEX | 6 format dokumen lain, DOCX, PDF | 3 MiB (3,1 MB) | Pandoc; PDF: Pandoc ke DOCX lalu LibreOffice |
| DOCX | TXT, MD, RTF, HTML, RST, TEX, PDF | 20 MiB (21 MB), isi XML maks. 5 MiB | python-docx, Pandoc, LibreOffice |
| PDF (berteks) | TXT, MD, DOCX | 20 MiB (21 MB), maks. 1.000 halaman | pypdfium2, Pandoc, python-docx |
| CSV, TSV | 4 format data lain | 25 MiB (26,2 MB) | Python `csv` |
| JSON | CSV, TSV, YAML, XML | 25 MiB (26,2 MB) | Python `json` (validasi ketat) |
| YAML | CSV, TSV, JSON, XML | 10 MiB (10,5 MB) | PyYAML |
| XML | CSV, TSV, JSON, YAML | 25 MiB (26,2 MB) | defusedxml, xmltodict |

Rincian per pasangan, aturan perataan data, dan hasil ukur ada di `PETUNJUK.md` bagian 5
sampai 9.

## Arsitektur

- `app.py` + `konversiku/`: API FastAPI dan UI satu halaman (`index.html`).
- PostgreSQL menyimpan status tugas (kode tugas hanya disimpan sebagai SHA-256).
- Redis + Celery mengantre tugas; pekerja Celery menjalankan mesin konversi.
- Pandoc dan LibreOffice dipanggil sebagai proses terpisah dengan batas waktu dan memori.
- `deploy/`: unit systemd (API, pekerja, timer pemulihan dan pembersihan), Nginx
  (batas unggah, rate limit), Prometheus dan dasbor Grafana.

## Menjalankan dan menguji

Laptop Windows (mesin konversi dan tes tanpa layanan):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
winget install --id JohnMacFarlane.Pandoc -e
winget install --id TheDocumentFoundation.LibreOffice -e
python -m pytest -m "not e2e"
```

Sistem tugas penuh butuh Linux dengan PostgreSQL, Redis, dan pekerja Celery. Langkah
pemasangan, variabel lingkungan, deploy, dan pemantauan ada di `PETUNJUK.md` bagian 3,
11, 13, 14, dan 15. Peta pemenuhan PRD ada di `STATUS_PRD.md`; output tes dan laporan
ukur kapasitas ada di `laporan_tes/`.

## Batasan

- Belum mendukung file 1 GB (butuh mesin konversi streaming).
- Data ke dokumen atau PDF (mis. CSV ke PDF) dan OCR untuk PDF hasil pindai belum ada.
- Layanan berjalan lewat HTTP di jaringan kampus; HTTPS dan nama domain belum.
- Hindari mengunggah dokumen rahasia. Siapa pun yang memegang kode tugas bisa mengunduh
  hasilnya selama 24 jam.
