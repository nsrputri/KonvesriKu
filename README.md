# KonversiKu

Prototipe konversi file berbasis server untuk tugas komputasi awan.

## Fitur yang tersedia

- CSV ke JSON.
- JSON ke CSV.
- DOCX ke TXT.
- TXT ke DOCX.
- Antarmuka lingkaran untuk unggah dan unduh hasil.
- `GET /api/formats` untuk daftar pasangan yang didukung.

File diproses oleh Python/FastAPI di server. Frontend memakai HTML, CSS, dan JavaScript.

## Batasan versi ini

Maksimal 2 MiB per file. CSV/JSON maksimal 10.000 record, teks UTF-8. JSON harus array objek datar dengan kunci yang sama; data bersarang ditolak. Nilai CSV tetap berupa teks. Nilai null pada JSON menjadi sel CSV kosong. Periksa formula sebelum membuka hasil CSV dalam spreadsheet.

DOCX hanya mengambil paragraf dan tabel utama, tanpa mempertahankan gambar, gaya, header/footer, atau tata letak. Isi DOCX setelah ekstraksi ZIP dibatasi 20 MiB dan 1.000 entri.

Konversi masih sinkron. Belum ada antrean persisten, kode tugas, penyimpanan hasil lintas sesi, atau penghapusan terjadwal. Versi ini belum memenuhi seluruh PRD dan tidak mendukung file 1 GB. Jangan gunakan untuk dokumen rahasia atau langsung mempublikasikannya sebagai layanan produksi.

## Menjalankan di Windows (PowerShell)

Jalankan dari folder repositori:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Buka http://127.0.0.1:8000 pada perangkat yang menjalankan aplikasi. Alamat ini bukan tautan publik.

## Menjalankan di Linux

Disarankan Python 3.12 dan paket venv yang sesuai.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

## Pemeriksaan konversi

Setelah memasang requirements:

```powershell
.\.venv\Scripts\python.exe selfcheck.py
```

Linux: `.venv/bin/python selfcheck.py`.

Pemeriksaan ini menguji handler empat pasangan secara langsung dan validasi dasar, bukan browser, jaringan, maupun deployment publik.

## Status pengembangan

Kode aplikasi pada baseline ini berasal dari tiga berkas sumber yang sebelumnya telah digunakan. Perluasan format dan sistem tugas cloud adalah pekerjaan berikutnya yang belum termasuk dalam versi ini.
