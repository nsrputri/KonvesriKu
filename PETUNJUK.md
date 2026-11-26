# KonversiKu tahap 2: petunjuk

Tahap 1 membangun **mesin konversi sesuai PRD Bagian 6** (72 pasangan). Tahap 2
mengubahnya menjadi **sistem tugas**: unggahan selesai, pengguna mendapat **kode
tugas**, tugas masuk **antrean** (PostgreSQL + Redis/Celery), **pekerja terpisah**
mengonversi, status tersimpan, hasil diunduh dari **sesi atau perangkat lain**,
input dihapus setelah selesai, dan hasil dihapus **24 jam setelah selesai**
(bagian 14). Sejak proyek 4 (26 November 2026) batas unggahan **per format**, 2 sampai
25 MiB, ditentukan dari pengukuran di VPS (bagian 9). Nginx lewat IP dan Grafana sudah
jalan (bagian 12 dan 15); HTTPS publik dan Bind9 belum. Peta lengkap terhadap `PRD.md`
ada di `STATUS_PRD.md`.

## 1. Isi paket

| Path | Isi |
| --- | --- |
| `app.py` | FastAPI `app:app`: `GET /`, `GET /api/formats`, API tugas, `/healthz`, `/metrics`. `POST /api/convert` (sinkron tahap 1) hanya aktif bila `KONVERSIKU_SYNC_ENDPOINT=1` (tes) |
| `index.html` | UI mandiri: lingkaran 280 px dengan pilihan format radial, kode tugas, "Punya kode?", daftar tugas di perangkat ini |
| `ikon/` | Ikon situs (favicon 16/32 px dan `.ico`, apple-touch-icon 180 px, Android 192/512 px, `site.webmanifest`), disajikan di root oleh `app.py` dari daftar tetap |
| `konversiku/jobs_api.py`, `ops_api.py`, `sync_api.py`, `web.py` | Router API tugas, pemantauan, endpoint sinkron khusus tes, header bersama |
| `konversiku/jobs.py`, `schema.sql`, `db.py`, `settings.py` | Status tugas di PostgreSQL (klaim atomik, retensi), koneksi, pengaturan dari variabel lingkungan |
| `konversiku/storage.py` | Folder data privat: unggahan streaming, input dan hasil per tugas |
| `konversiku/celery_app.py`, `worker.py` | Aplikasi Celery dan logika pekerja (retry terbatas) |
| `konversiku/recovery.py`, `cleanup.py` | Perintah berkala: pemulihan tugas macet dan pembersihan retensi |
| `deploy/systemd/` | Unit API, pekerja, timer pemulihan dan pembersihan, contoh `konversiku.env` (terpasang di VPS sejak 25 November 2026) |
| `deploy/dev/` | `dev.sh` (stack dev di tmux, port 8001) dan `uji_asap.sh` (uji asap HTTP) |
| `konversiku/registry.py` | Matriks 72 pasangan, catatan per pasangan, alasan nonaktif |
| `konversiku/documents.py` | Dokumen: TXT, MD, RTF, HTML, RST, TEX, DOCX, PDF |
| `konversiku/pdf_extract.py` | Pembaca text layer PDF (pypdfium2) yang dijalankan sebagai proses terpisah |
| `konversiku/data.py` | Data: CSV, TSV, JSON, YAML, XML, aturan perataan, konvensi XML |
| `konversiku/tools.py` | Deteksi Pandoc/LibreOffice dan subprocess aman |
| `konversiku/ooxml.py`, `strictjson.py`, `limits.py`, `common.py` | Cek ZIP DOCX, JSON ketat, batas, utilitas |
| `requirements.txt`, `requirements-dev.txt` | Dependency runtime dan tes |
| `tests/` | Tes mesin tahap 1, tes tahap 2 `tests/tahap2/`, tes browser `tests/e2e/`, skrip `tests/ukur_kapasitas.py` |
| `contoh/` | Satu contoh kecil nonrahasia per format sumber |
| `laporan_tes/` | Output pytest aktual, hasil ukur kapasitas, screenshot UI |

## 2. Setup Windows (pengembangan)

Butuh Python 3.11 atau lebih baru.

```powershell
cd <folder KonversiKu_Tahap2>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
winget install --id JohnMacFarlane.Pandoc -e
winget install --id TheDocumentFoundation.LibreOffice -e
python -m pytest -m "not e2e"
```

Di laptop Windows, tahap 2 dipakai untuk menulis kode dan menjalankan tes mesin
konversi serta tes tahap 2 yang tidak butuh layanan. Sistem tugas penuh butuh
PostgreSQL, Redis, dan pekerja Celery (Celery tidak resmi mendukung Windows), jadi
dijalankan dan dites di VPS (bagian 14), lalu dibuka dari laptop lewat tunnel SSH.
LibreOffice dicari otomatis di `C:\Program Files\LibreOffice\program\soffice.com`;
lokasi lain diatur dengan `KONVERSIKU_SOFFICE`. Pandoc dicari di PATH atau
`KONVERSIKU_PANDOC`.

## 3. Setup Linux (Ubuntu 24.04, Python 3.12)

```bash
cd ~/konversiku
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo apt install libreoffice-writer-nogui fonts-dejavu-core postgresql redis-server
sudo dpkg -i pandoc-3.13-1-amd64.deb   # rilis resmi, bukan pandoc dari apt (lihat di bawah)
pandoc --list-input-formats | grep -xE 'gfm|rtf|html|rst|latex|docx'
pandoc --list-output-formats | grep -xE 'plain|gfm|rtf|html5|rst|latex|docx'
# PostgreSQL, Redis, folder data, dan unit systemd: bagian 13 dan 14
```

Dua perintah `grep` di atas menunjukkan reader/writer yang benar-benar ada di
Pandoc versi distro. Aplikasi memeriksanya sendiri saat start. Reader atau writer
yang tidak ada atau gagal di mode `--sandbox` membuat pasangan terkait nonaktif,
dan alasannya tampil di UI. Setelah memasang alat, restart service.

**Penting, terbukti di VPS kampus (25 November 2026): jangan pakai Pandoc dari apt
Ubuntu 24.04 (3.1.3).** Pembaca RTF-nya menghilangkan huruf setelah karakter Unicode
("Ñandú" menjadi "Ñndú"), dan karena data file-nya terpisah, writer DOCX gagal di
`--sandbox` sehingga 10 pasangan ke DOCX/PDF nonaktif. Pasang rilis resmi
`pandoc-3.13-1-amd64.deb` dari github.com/jgm/pandoc (cocokkan SHA-256 di halaman
rilis) dengan `sudo dpkg -i`. Dengan Pandoc 3.13 dan LibreOffice 24.2.7 dari apt,
seluruh tes mesin tahap 1 lulus di Ubuntu 24.04.

## 4. Alat sistem dan dependency

| Alat | Lisensi | Dipakai untuk | Pemanggilan |
| --- | --- | --- | --- |
| Pandoc (diuji 3.13) | GPL-2.0-or-later, program terpisah | Membaca MD (gfm), RTF, HTML, RST, LaTeX, DOCX dan menulis TXT (plain), MD, RTF, HTML, RST, LaTeX, DOCX | `pandoc +RTS -M1536M -RTS --sandbox -f <reader> -t json <tmp>/input.<ext>` lalu `pandoc ... --sandbox -f json -t <writer> -o -`, timeout 120 detik per panggilan |
| LibreOffice (diuji 26.8.3.1) | MPL-2.0, program terpisah | Semua target PDF (DOCX ke PDF) | `soffice -env:UserInstallation=<profil proses> --headless --norestore --nolockcheck --nologo --convert-to pdf:writer_pdf_Export --outdir <tmp> <tmp>/input.docx`, timeout 360 detik |

Flag Pandoc (`--sandbox`, `-o -`, `+RTS -M...`) mengikuti manual resmi pandoc.org
bagian "A note on security". Flag LibreOffice mengikuti halaman resmi "Starting
LibreOffice Software With Parameters". Kemampuan Pandoc dicek dengan
`--list-input-formats`/`--list-output-formats`, lalu setiap writer diuji
sungguhan dalam `--sandbox`. Untuk build distro tanpa data tertanam, aplikasi
mencoba `--reference-doc`/`--template` bawaan.

Dependency Python (versi yang diuji):

| Paket | Versi | Lisensi | Catatan |
| --- | --- | --- | --- |
| fastapi | 0.165.0 | MIT | |
| uvicorn | 0.54.0 | BSD-3-Clause | |
| python-docx | 1.2.0 | MIT | TXT/PDF ke DOCX, DOCX ke TXT |
| PyYAML | 6.0.4 | MIT | Turunan SafeLoader |
| pypdfium2 | 5.15.2 | Apache-2.0 / BSD-3-Clause | Text layer PDF (PDFium) |
| defusedxml | 0.7.1 | PSF-2.0 | XML aman |
| xmltodict | 1.0.4 | MIT | Konvensi XML ke data |
| celery (dengan kombu, redis-py) | 5.7.0 (kombu 5.7.0, redis 8.2.0) | BSD-3-Clause, MIT | Antrean tugas tahap 2 |
| psycopg (+ psycopg-binary) | 3.3.9 | LGPL-3.0 | Status tugas di PostgreSQL |
| prometheus-client | 0.26.0 | Apache-2.0 | `/metrics` |
| Khusus tes: pytest, httpx, markdown-it-py, docutils, pylatexenc, pypdf, playwright | 9.2.0, 0.28.1, 4.3.0, 0.23, 2.11, 7.1.1, 1.65.2 | MIT, BSD-3, MIT, Public Domain/BSD, MIT, BSD-3, Apache-2.0 | |

Layanan sistem tahap 2 (diuji di VPS): PostgreSQL 16.15 dan Redis 7.0.15 dari apt
Ubuntu 24.04, keduanya hanya mendengarkan localhost; Redis memakai password dan AOF.

Penyimpangan dari teknologi usulan PRD:
- **PyMuPDF diganti pypdfium2.** Pada uji nyata, pypdf dan pdfminer.six salah
  membaca huruf beraksen ("Ñandú") dari PDF buatan LibreOffice dengan font Cambria.
  PyMuPDF dan pypdfium2 membaca dengan benar. pypdfium2 dipilih karena lisensinya
  permisif; PyMuPDF berlisensi AGPL-3.0.
- **pandas tidak dipakai.** CSV/TSV memakai modul `csv` stdlib: lebih ringan,
  dan nilai tetap teks tanpa tebakan tipe.

## 5. Matriks aktual (72 pasangan, dihitung dari `konversiku/registry.py`)

| Sumber | Target | Alat yang dibutuhkan |
| --- | --- | --- |
| TXT | MD, RTF, HTML, RST, TEX, DOCX, PDF | HTML, DOCX: tidak ada. PDF: LibreOffice. Lainnya: Pandoc |
| MD (.md, .markdown) | TXT, RTF, HTML, RST, TEX, DOCX, PDF | Pandoc; PDF juga LibreOffice |
| RTF | TXT, MD, HTML, RST, TEX, DOCX, PDF | Pandoc; PDF juga LibreOffice |
| HTML (.html, .htm) | TXT, MD, RTF, RST, TEX, DOCX, PDF | Pandoc; PDF juga LibreOffice |
| RST | TXT, MD, RTF, HTML, TEX, DOCX, PDF | Pandoc; PDF juga LibreOffice |
| TEX | TXT, MD, RTF, HTML, RST, DOCX, PDF | Pandoc; PDF juga LibreOffice |
| DOCX | TXT, MD, RTF, HTML, RST, TEX, PDF | TXT: tidak ada. PDF: LibreOffice. Lainnya: Pandoc |
| PDF (text layer) | TXT, MD, DOCX | TXT, DOCX: tidak ada. MD: Pandoc |
| CSV | TSV, JSON, YAML, XML | tidak ada |
| TSV | CSV, JSON, YAML, XML | tidak ada |
| JSON | CSV, TSV, YAML, XML | tidak ada |
| YAML (.yaml, .yml) | CSV, TSV, JSON, XML | tidak ada |
| XML | CSV, TSV, JSON, YAML | tidak ada |

Rinciannya 52 pasangan dokumen dan 20 pasangan data. Keempat pasangan lama (CSV
ke JSON, JSON ke CSV, DOCX ke TXT, TXT ke DOCX) tetap ada dan tidak butuh alat
sistem. Tanpa Pandoc dan LibreOffice, 25 pasangan tetap aktif dan 47 dinonaktifkan
dengan alasan. Di laptop uji, 72 dari 72 aktif.

Tidak didukung, sesuai PRD: data ke dokumen/PDF, PDF ke RTF/HTML/RST/TEX/PDF,
DOCX ke DOCX, OCR, gambar, audio, video. Kombinasi di luar matriks ditolak dengan
pesan "Pasangan format tidak didukung".

## 6. Cara kerja pipeline dokumen

- **TXT dan teks hasil PDF** di-escape ke HTML lalu dibaca Pandoc, sehingga karakter
  khusus Markdown/RST/LaTeX tetap menjadi teks, bukan format. TXT ke DOCX memakai
  python-docx (satu baris satu paragraf, perilaku lama). TXT ke HTML dibuat Python.
- **MD, RTF, HTML, RST, TeX, DOCX** dibaca reader Pandoc (Markdown memakai `gfm`,
  yang menurut manual Pandoc lebih tahan input patologis). AST lalu disanitasi:
  gambar diganti teks alt, blok HTML/LaTeX mentah dihapus, atribut HTML dihapus,
  tautan berskema selain http/https/mailto menjadi teks.
- **Target PDF**: dokumen ditulis ke DOCX (Pandoc, atau python-docx untuk TXT/PDF),
  lalu LibreOffice membuat PDF. Tidak ada LaTeX engine, dan kode LaTeX pengguna
  tidak pernah dijalankan.
- **PDF input**: pypdfium2 membaca text layer di proses Python terpisah (`-I`,
  timeout 120 detik, batas memori 1 GiB di Linux). PDF tanpa teks, terenkripsi,
  atau rusak ditolak.

## 7. Aturan data: perataan ke CSV/TSV dan konvensi XML

Perataan ke CSV/TSV (berlaku untuk JSON, YAML, XML sebagai sumber):

| Data | Kolom/sel CSV |
| --- | --- |
| `[{"a": 1}, {"a": 2}]` | satu baris per objek |
| `{"a": 1}` (satu objek) | satu baris |
| `{"data": {"item": [...]}}` | pembungkus satu-kunci dibuka sampai ketemu list; jalur `data.item` dilaporkan |
| `{"user": {"name": "A"}}` | kolom `user.name` |
| `{"tags": ["x", "y"]}` | kolom `tags[0]`, `tags[1]` |
| `{}` / `[]` | sel berisi teks `{}` / `[]` |
| `null`, kunci tidak ada di baris itu | sel kosong (jumlahnya dilaporkan) |
| `true`/`false`, angka | `true`/`false`, angka apa adanya |
| kunci `a.b` dan jalur `a` ke `b` di baris yang sama | **ditolak**, nama kolom disebut |
| elemen array yang bukan objek, data skalar | **ditolak** dengan pesan spesifik |

CSV/TSV sebagai sumber: semua nilai tetap teks (001 tetap 001), dan header bertitik
tidak dibuka kembali menjadi objek. Pengaman formula: sel yang diawali `=`, `+`,
`-`, `@`, tab, atau CR (kecuali angka biasa seperti `-5`) diberi awalan `'`.
Pengaman ini aktif bawaan dan bisa dimatikan lewat centang di UI atau
`formula_guard=0`; jumlah sel yang diubah selalu dilaporkan.

Konvensi XML ke data (xmltodict, setelah lolos pemeriksaan defusedxml):

```xml
<katalog xmlns:x="urn:x"><buku id="1"><judul>  Laskar </judul><x:tag>a</x:tag><kosong/></buku>
<buku id="2"><judul>B</judul></buku><catatan tipe="info">teks</catatan></katalog>
```

```json
{"katalog": {"@xmlns:x": "urn:x",
  "buku": [{"@id": "1", "judul": "  Laskar ", "x:tag": "a", "kosong": null}, {"@id": "2", "judul": "B"}],
  "catatan": {"@tipe": "info", "#text": "teks"}}}
```

Atribut menjadi `@nama`, teks elemen yang beratribut menjadi `#text`, elemen
berulang menjadi list, elemen kosong menjadi `null`, dan semua nilai menjadi teks
(spasi di dalam teks dipertahankan). Komentar, processing instruction, dan DTD
tidak dibawa. Konten campuran (`<p>a<b>x</b>c</p>`) ditolak karena urutannya akan
hilang.

Data ke XML: objek satu-kunci menjadi root; selain itu dibungkus `<data>`, dan
item list tingkat atas menjadi `<item>`. List di dalam list menjadi elemen `<item>`.
`null`, `[]`, dan `{}` menjadi elemen kosong. Nama kunci yang bukan nama XML sah
(mis. `nama lengkap`, `1abc`) ditolak dengan namanya. Round-trip tidak dijamin:
list berisi satu elemen terbaca kembali sebagai objek, dan tipe menjadi teks.

YAML dibaca dengan skema YAML 1.2 core: `yes`, `no`, tanggal, dan `1:30` tetap teks.
YAML ditulis tanpa alias. Komentar YAML tidak dibawa.

## 8. Batasan dan kehilangan data (juga tampil di UI sebelum konversi)

- Gambar di dokumen tidak diproses (diganti teks alt). Gaya kompleks, header/footer,
  dan tata letak bisa hilang. Ke TXT, format hilang.
- PDF ke TXT/MD/DOCX hanya mengambil teks; tata letak, tabel, dan kolom PDF tidak
  dijaga. PDF hasil pindai ditolak (tanpa OCR).
- PDF hasil (lewat DOCX dan LibreOffice) memakai tata letak sederhana dan font di server.
- LaTeX tidak dikompilasi; `\input`, `\include`, makro paket, dan gambar tidak
  diproses; hanya struktur yang dikenali Pandoc.
- Validasi isi: RTF wajib diawali `{\rtf`, HTML wajib punya tag, DOCX wajib ZIP
  OOXML yang valid, PDF wajib bertanda `%PDF-`, teks wajib UTF-8 tanpa karakter
  biner. TXT, Markdown, RST, dan LaTeX **tidak bisa dibedakan dengan pasti** dari
  isinya: file `.md` berisi teks biasa tetap diproses sebagai Markdown.

## 9. Batas pemrosesan dan kapasitas terukur

Batas unggah per format sumber (stream, berhenti begitu lewat; 413 menyebut formatnya,
mis. "File CSV melebihi batas 25 MiB (26,2 MB)."). Alias memakai batas format kanoniknya
(`.yml` = YAML, `.markdown` = MD, `.htm` = HTML). Batas dihitung biner: 1 MiB = 1.048.576
byte; pesan dan UI menyertakan MB desimal (1 MB = 1.000.000 byte) karena lebih dikenal.
Windows Explorer menghitung biner tetapi menuliskannya "MB", jadi file "25 MB" di Windows
tepat 25 MiB. Angka memori di bagian ini juga MiB, kecuali yang ditulis GB.

| Format sumber | Batas unggah | Batas isi tambahan |
| --- | --- | --- |
| TXT | 2 MiB (2,1 MB) | |
| MD, RTF, HTML | 4 MiB (4,2 MB) | |
| RST, TEX | 3 MiB (3,1 MB) | |
| DOCX | 20 MiB (21 MB) | Semua bagian berisi XML (isi, gaya, header, catatan; dikenali dari isinya) maks. 5 MiB (5,2 MB) setelah dibuka; isi utama wajib di `word/document.xml` |
| PDF | 20 MiB (21 MB) | 1.000 halaman dan 5.000.000 karakter teks |
| CSV, TSV, JSON, XML | 25 MiB (26,2 MB) | |
| YAML | 10 MiB (10,5 MB) | 1.750.000 node saat dibaca (ditolak sebelum seluruh struktur dibangun) |

| Batas pendamping | Nilai |
| --- | --- |
| Hasil | 100 MiB |
| Teks hasil ekstraksi DOCX/PDF | 5.000.000 karakter |
| Tabel CSV/TSV (termasuk hasil perataan) | 500.000 baris, 200 kolom, 2.000.000 sel |
| ZIP DOCX | 1.000 entri, 64 MiB setelah dibuka (gambar termasuk), tanpa entri terenkripsi/nama tidak aman |
| JSON/YAML/XML | nesting 64, 2.500.000 node (setelah alias diperluas), 50 alias YAML, angka maks. 4.300 digit |
| PDF | timeout 120 detik, memori pembaca 1 GiB (Linux) |
| Pandoc | heap 1,5 GiB (`1536M`), timeout 120 detik per panggilan |
| LibreOffice | timeout 360 detik (2 kali kasus terlama terukur), satu konversi per proses aplikasi |
| Konkurensi konversi | 2 per node pekerja Celery; sisanya menunggu di antrean; unit pekerja `MemoryMax=5G` |
| Antrean dan disk | Submit ditolak 503 bila tugas `queued` mencapai 50 atau folder data mencapai 5 GB |
| Batas waktu tugas | Lunak 600 detik, keras 660 detik; lease 720 detik; retry maksimal 3 percobaan untuk kegagalan di luar file |
| Nginx | `client_max_body_size 26m` (batas terbesar + 1 MiB), jeda klien dan proxy 300 detik |

**Kapasitas terukur di VPS (proyek 4).** Aturan memilih batas: ukuran terbesar yang diuji
dengan waktu <= 300 detik dan beban memori <= 1.500 MiB (sekitar 1,57 GB) per tugas untuk target terberat dari
format sumber itu, dibulatkan ke bawah. Beban memori = jumlah RSS seluruh pohon proses satu
tugas (proses Python yang memegang AST ditambah Pandoc atau LibreOffice yang sedang berjalan),
disampel tiap 0,1 detik; halaman bersama terhitung ganda, jadi angkanya sedikit melebihi
pemakaian nyata. VPS: 2 inti, RAM 7,8 GiB, Pandoc 3.13, LibreOffice 24.2.7. Laporan:
- `laporan_tes/kapasitas_proyek4.txt` (`tests/ukur_kapasitas_proyek4.py`): ukur pertama
  dengan batas dilonggarkan, 3 sampai 20 MiB untuk dokumen dan 10 sampai 50 MiB untuk data.
  Dokumen teks 10 MiB memakai sekitar 1.600 MiB (Pandoc), 5 MiB 750 sampai 1.050 MiB. CSV
  50 MiB ke XML 1.784 MiB, JSON 50 MiB ke YAML 2.468 MiB, YAML 25 MiB ke JSON 2.911 MiB.
- `laporan_tes/kapasitas_proyek4.txt` dan `laporan_tes/kapasitas_proyek4_verifikasi.txt`
  memakai metrik lama (RSS satu proses terbesar) dan **terlalu rendah** untuk pasangan lewat
  Pandoc (temuan review akhir). Disimpan sebagai riwayat; angka di sana bukan dasar batas akhir.
- `laporan_tes/kapasitas_proyek4_verifikasi_pohon.txt` (`tests/verifikasi_batas_proyek4.py`):
  dasar batas akhir. Semua 72 pasangan dengan file sebesar batas, ditambah TXT berbaris
  pendek, DOCX di batas XML, DOCX bergambar 19,5 MB, PDF 1.000 halaman, PDF bergambar 19,5 MB,
  dan bentuk padat (YAML angka, JSON objek kecil, XML elemen kosong, CSV satu kolom, CSV 200
  kolom). Dokumen teks di batas lama melewati 1.500 MiB (mis. MD 5 MiB ke TXT 1.776 MiB), sehingga
  batas diturunkan; ukur tambahan TXT 2 MiB dan XML DOCX 5 MiB lolos. Pada batas akhir: beban
  terbesar 1.500 MiB atau 1,57 GB (RTF 4 MiB ke MD), terlama 73 detik (TXT 2 MiB berbaris pendek ke PDF).
  Bentuk padat ditolak dengan pesan batas dalam 0,5 sampai 47 detik, beban <= 1.107 MiB.
- Bukti produksi lewat Nginx (`laporan_tes/proyek4_bukti_produksi.txt`): CSV tepat 25 MiB
  (451.956 baris) diunggah dari laptop lewat VPN dalam 9 detik, selesai, hasil JSON 57,9 MB
  benar; 1 byte di atas batas ditolak 413 dengan nama format (CSV, YAML lewat alias `.yml`, TXT).
- Dua tugas terberat bersamaan di produksi (`laporan_tes/proyek4_bersamaan_produksi.txt`,
  `tests/uji_bersamaan_proyek4.py`): RTF 4 MiB ke MD dan MD 4 MiB ke TXT selesai dalam 32 dan
  39 detik; puncak memori seluruh cgroup unit pekerja (`MemoryPeak`, termasuk cache file)
  2,61 GB (2.490 MiB) dari `MemoryMax` 5 GiB.

Keterbatasan yang tercatat:
- Batas dihitung dari isi prosa (paragraf biasa). Dokumen yang sangat padat struktur
  (puluhan ribu judul atau butir daftar pendek) bisa ditolak lebih awal: Markdown padat
  3 MiB ke DOCX kehabisan heap Pandoc dan dibalas "Dokumen terlalu besar atau terlalu
  kompleks". Dokumen berisi ratusan ribu paragraf sangat pendek ke PDF bisa melewati batas
  waktu LibreOffice (TXT 4 MiB berbaris 26 byte ke PDF melewati 240 detik pada ukur awal).
- Tabel sangat lebar dengan nama kolom panjang bisa melewati batas hasil 100 MiB jauh di bawah
  batas unggah: CSV 3,8 MiB, 200 kolom bernama 40 karakter, ke JSON/XML ditolak "Hasil
  konversi terlalu besar"; ke TSV/YAML lolos.
- Batas dokumen teks (2 sampai 4 MiB) ditentukan oleh memori: proses Python memegang AST
  sementara Pandoc menulis. Melepas AST sebelum penulisan bisa menaikkan batas ini (belum
  dikerjakan).
- TXT ke DOCX dulu kuadratik terhadap jumlah baris (python-docx `add_paragraph`); kini
  linear (`test_txt_to_docx_many_short_lines_is_linear`).

Kapasitas tahap 1 di laptop Windows (`laporan_tes/kapasitas.txt`, ulangi dengan
`python tests/ukur_kapasitas.py`), sebelum proyek 4:

- Ukuran terbesar yang diuji adalah **batas 2 MiB** (2.096.128 byte).
- Markdown prosa 2 MiB ke HTML: 10,7 detik. Ke DOCX: 11,5 detik. Ke PDF: 45,2 detik
  (PDF 2,2 MB).
- Markdown 2 MiB yang sangat padat struktur (sekitar 20 ribu judul) ke HTML
  memakan 23,8 detik, mendekati timeout. Ke DOCX ditolak dengan 413 karena heap
  Pandoc habis, bahkan pada heap 2 GiB saat dicoba manual.
- CSV 10.000 baris ke JSON: 0,1 detik. JSON 10.000 objek bersarang ke CSV:
  0,2 detik; ke XML: 1,0 detik.
- Profil LibreOffice per proses: sekitar 0,3 MiB.

Target 1 GB per file dari PRD **belum tercapai**. Pandoc membaca seluruh dokumen ke
memori (bersama AST di Python, dokumen teks 4 MiB sekitar 1.500 MiB), dan parser data Python
memuat seluruh struktur. File ratusan MiB sampai 1 GB butuh mesin konversi streaming,
yang di luar cakupan proyek 4.

## 10. Keamanan

- **Tanpa fetch resource.** Pandoc selalu memakai `--sandbox`. Uji nyata memakai
  server HTTP lokal yang mencatat request dan file rahasia lokal: HTML (iframe,
  img, CSS), Markdown, RST (`.. include::`, `.. image::`), dan LaTeX (`\input`,
  `\include`, `\includegraphics`) tidak mengambil apa pun. Uji mutasi menunjukkan
  bahwa tanpa `--sandbox`, RST dan LaTeX membocorkan file lokal dan HTML mengambil
  iframe, sehingga tes itu gagal.
- **Sanitasi.** AST disanitasi sebelum ditulis. Uji mutasi tanpa sanitasi
  meloloskan `<script>`/`javascript:`, dan tesnya gagal.
- **DOCX.** Jumlah entri, total ukuran, CRC, nama entri, dan enkripsi diperiksa
  sebelum dimuat, tanpa ekstraksi ke disk. Relasi eksternal selain hyperlink dan
  field `INCLUDETEXT`/`INCLUDEPICTURE`/`LINK`/`DDE`/`IMPORT` ditolak. ZIP ditulis
  ulang sebelum diberikan ke Pandoc/LibreOffice.
- **XML/YAML/JSON.** DTD, entitas, dan referensi eksternal ditolak (defusedxml;
  uji mutasi tanpa defusedxml gagal). YAML memakai turunan SafeLoader tanpa objek
  Python. Duplicate key, NaN/Infinity/overflow, alias berlebih, dan nesting
  berlebih ditolak.
- **Subprocess.** Argumen berupa list tanpa `shell=True`, reader/writer dari
  allowlist, file input bernama tetap (`input.<ext>`), dan timeout. Saat timeout,
  pohon proses dihentikan (`taskkill /T` atau `killpg`). Kehabisan heap
  menghasilkan pesan 413 yang jelas. PDF dibaca di proses terpisah. Profil
  LibreOffice unik per proses dan dihapus saat timeout atau keluar.
- **File sementara.** Direktori per request dihapus di `finally`, baik sukses,
  error, maupun timeout (diuji).
- **Respons.** `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `Content-Disposition: attachment`, dan CSP `sandbox`. HTML hasil membawa meta CSP
  `default-src 'none'`. UI membuat Blob `application/octet-stream` dan tidak
  pernah merender hasil. Nama file dan pesan ditampilkan dengan `textContent`.
- **Log.** Isi file tidak dicatat. Access log uvicorn mencatat URL (`source`,
  `target`), bukan isi.
- **Kode tugas (tahap 2).** `secrets.token_urlsafe(32)` (256 bit). Database hanya
  menyimpan SHA-256-nya. Kode dikirim lewat header `X-Kode-Tugas`, tidak pernah di
  URL, query, log, pesan Celery, atau metrik (diuji dengan membaca log uvicorn,
  pekerja, dan pemulihan). Path file diturunkan dari UUID tugas, bukan dari masukan.
- **Folder data dan layanan (tahap 2).** Folder data izin 700, file 600. PostgreSQL
  lewat socket lokal; Redis hanya localhost dengan password dari file 600.
  `/healthz` dan `/metrics` hanya untuk jaringan lokal; Nginx (proyek berikutnya)
  tidak boleh meneruskan `/metrics` ke publik.
- API tidak lagi mengonversi; konversi berjalan di pekerja terpisah, dan operasi
  database API berjalan di thread pool sehingga event loop tidak macet.

## 11. Tes

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -m "not e2e"                       # laptop: tes mesin + tes tahap 2 tanpa layanan
KONVERSIKU_WAJIB_LAYANAN=1 python -m pytest -m "not e2e"   # VPS: semua, wajib ada PostgreSQL/Redis
KONVERSIKU_E2E_URL=http://127.0.0.1:8001 python -m pytest tests/e2e   # browser ke stack dev
```

- Tes bertanda `layanan` butuh PostgreSQL dan Redis (schema uji sementara, Redis
  database 1). Tanpa layanan, tes itu **SKIPPED "BELUM DIUJI"**; dengan
  `KONVERSIKU_WAJIB_LAYANAN=1` kondisi itu menjadi kegagalan. Tes bertanda `proses`
  menjalankan uvicorn (socket UNIX) dan pekerja Celery sungguhan, hanya di Linux.
- Tes mesin tahap 1 memakai endpoint sinkron `/api/convert` yang dipasang hanya
  oleh `tests/conftest.py` (`KONVERSIKU_SYNC_ENDPOINT=1`); aplikasi produksi tidak
  memilikinya (diuji `tests/test_sync_endpoint_gate.py`).
- Tes E2E memakai Google Chrome di laptop terhadap stack dev di VPS lewat tunnel
  `ssh -N -L 8001:127.0.0.1:8001 b2-3@10.33.109.96`.
- Bila alat sistem tidak ada, tes pasangan terkait tercatat **SKIPPED "BELUM
  DIUJI"**, tidak dihitung lulus.

Hasil tahap 2 (25 November 2026), output lengkap di `laporan_tes/`:
- `tahap2_vps.txt`: VPS Ubuntu 24.04.5, Python 3.12.3, Pandoc 3.13, LibreOffice
  24.2.7, PostgreSQL 16.15, Redis 7.0.15: **295 passed, 0 failed, 0 skipped**.
- `tahap2_laptop.txt`: Windows 11, Python 3.11.9: **235 passed, 60 skipped** (tes
  `layanan` yang sama, lulus di VPS), 0 failed.
- `tahap2_e2e.txt`: Chrome ke stack dev: **19 passed**, termasuk tutup browser lalu
  unduh dengan kode di sesi baru, localStorage rusak, klik ganda, dan 320/375/768 px.
- Tes tahap 2 mencakup klik atomik dua koneksi bersamaan, pekerja dibunuh
  `SIGKILL` lalu pulih (uji mutasi: tanpa pemulihan saat mulai, tes gagal), restart
  API, dua pekerja dengan pesan duplikat, retry terbatas, retensi dengan jam palsu,
  unggahan putus, PostgreSQL mati, Redis mati, dan kode tidak ada di log.

Hasil tahap 1 (Windows 11, Python 3.11.9, Pandoc 3.13, LibreOffice 26.8.3.1,
Chrome): **228 passed, 0 failed, 0 skipped**, tersimpan di
`laporan_tes/pytest_output.txt`. Isinya:

- 52/52 pasangan dokumen dan 20/20 pasangan data diuji dari input nyata lewat API,
  dan outputnya diparse dengan parser yang sesuai format (python-docx, pypdfium2,
  markdown-it, docutils, pylatexenc, html.parser, Pandoc untuk RTF, json, csv,
  PyYAML, defusedxml).
- 15 tes browser.

Catatan jujur tentang tes browser:
- Drag and drop diuji dengan event `drop` sintetis, bukan drag dari Windows Explorer.
- Chrome yang dikendalikan Playwright mengubah respons `fetch()` berisi PDF ber-header
  `attachment` menjadi 204 kosong. Harness meneruskan respons server apa adanya
  (status, header, isi) lewat `route.fetch()`. Di browser Chromium tanpa Playwright,
  header yang sama diuji manual dan menghasilkan 200 dengan isi utuh.
- Screenshot ada di `laporan_tes/screenshots/` dan sudah diperiksa visual
  (320/375/768/1024 px).

Hasil proyek 4 (26 November 2026, setelah perbaikan review akhir): VPS **314 passed**,
laptop **253 passed, 61 skipped** (tes `layanan`), E2E ke stack dev **20 passed**
(`laporan_tes/proyek4_e2e_dev.txt`) dan ke produksi lewat `http://10.33.109.96`
**20 passed** (`laporan_tes/proyek4_e2e_produksi.txt`).

Belum diuji: file di atas batas per format (sengaja ditolak), uji beban banyak pengguna,
kehabisan memori pada unit pekerja (`MemoryMax`), HTTPS publik, dan
reboot VPS (sengaja tidak dilakukan pada server bersama; pemulihan setelah restart
diuji dengan mematikan dan menjalankan ulang proses).

## 12. HTTPS dan origin

Aplikasi hanya mendengarkan `127.0.0.1:8000` (loopback). Sejak 25 November 2026
**Nginx 1.24** menjadi pintu masuk jaringan kampus di **`http://10.33.109.96`** (port 80,
HTTP; HTTPS menyusul setelah keputusan domain). Konfigurasi di `deploy/nginx/`
(`/etc/nginx/sites-available/konversiku` dan `/etc/nginx/konversiku-proxy.conf`; situs
`default` dinonaktifkan):
- Meneruskan ke `127.0.0.1:8000`; unggahan ditampung Nginx dulu (maks. 26 MiB, jeda klien
  300 detik), di atas itu 413 JSON "File melebihi batas 25 MiB (26,2 MB), batas terbesar; ...".
  Di bawahnya aplikasi menolak per format dengan pesan yang menyebut formatnya.
- Rate limit per IP: unggahan `POST /api/jobs` 20 per menit (lonjakan 20), permintaan
  lain 20 per detik (lonjakan 40); kelebihan dibalas 429 JSON.
- `/metrics` dan `/healthz` hanya dari dalam VPS (403 dari jaringan); versi Nginx tidak
  ditampilkan. API mati dibalas 503 JSON.
- Diuji: 19 tes browser langsung ke `http://10.33.109.96` lulus
  (`laporan_tes/tahap2_e2e_nginx.txt`).

HTTPS publik lewat Cloudflare Tunnel **masih gagal** dan bukan bagian tahap ini. Jangan
mematikan verifikasi TLS. Bukti yang
masih diperlukan setelah tunnel diperbaiki: `curl -I https://<domain>/` dari
jaringan luar menunjukkan sertifikat valid dan header `nosniff`/`no-store`, serta
`https://<domain>/api/formats` terjangkau dari perangkat lain.

## 13. Deploy manual tahap 2 ke VPS kampus

**Dijalankan 25 November 2026** atas izin Putri (`~/konversiku-dev/unduhan/deploy_produksi.sh`,
log `~/konversiku-dev/log/deploy_produksi.log`). Prototipe lain yang sempat dipasang di
`~/konversiku` hari itu dan unit lama di-backup ke `~/konversiku.backup-20261125-130923`.
Langkah 5 (tes di folder produksi) dilewati karena kode yang sama sudah lulus di VPS
(folder dev), dan verifikasi memakai `/healthz`, uji asap, dan 19 tes browser ke port 8000.
Stack dev dihentikan sehingga hanya ada satu layanan. Hanya dengan izin eksplisit Putri. Semua langkah `sudo`
disebutkan dulu. **Jangan restart VPS** dan jangan mematikan semua proses Python;
yang dihentikan hanya layanan `konversiku`. Prasyarat yang sudah ada di VPS:
PostgreSQL 16, Redis 7 berpassword, Pandoc 3.13 rilis resmi, LibreOffice 24.2.7.

```bash
# 0. cek layanan yang benar
systemctl status konversiku --no-pager
# 1. backup versi berjalan
cp -a ~/konversiku ~/konversiku.backup-$(date +%Y%m%d-%H%M%S)
# 2. hentikan hanya layanan proyek ini
sudo systemctl stop konversiku
# 3. salin kode baru (venv lama tetap), lalu dependency
unzip ~/KonversiKu_Tahap2.zip -d ~/konversiku-tahap2
rsync -a --delete --exclude '.venv' --exclude 'konversiku.env' --exclude '.redis_password' \
  ~/konversiku-tahap2/KonversiKu_Tahap2/ ~/konversiku/
cd ~/konversiku && .venv/bin/pip install -r requirements.txt
# 4. database produksi, folder data, konfigurasi (sekali saja)
sudo -u postgres createdb -O b2-3 konversiku
mkdir -p ~/konversiku-data && chmod 700 ~/konversiku-data
install -m 600 ~/konversiku-dev/.redis_password ~/konversiku/.redis_password
install -m 600 deploy/systemd/konversiku.env.contoh ~/konversiku/konversiku.env
set -a; . ~/konversiku/konversiku.env; set +a
.venv/bin/python -c "from konversiku import db, settings; db.install_schema(db.connect(settings.load_settings()))"
# 5. tes tanpa browser (butuh requirements-dev)
.venv/bin/pip install -r requirements-dev.txt && KONVERSIKU_WAJIB_LAYANAN=1 .venv/bin/python -m pytest -m "not e2e"
# 6. pasang unit (menggantikan isi unit konversiku lama), lalu jalankan
sudo install -m 644 deploy/systemd/konversiku*.service deploy/systemd/konversiku*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now konversiku konversiku-pekerja konversiku-pemulihan.timer konversiku-pembersihan.timer
# 7. verifikasi
curl -s http://127.0.0.1:8000/healthz
bash deploy/dev/uji_asap.sh http://127.0.0.1:8000
```

Setelah setiap pembaruan kode, **restart API dan pekerja**
(`sudo systemctl restart konversiku konversiku-pekerja`). Proses yang direktori
kerjanya diganti di bawahnya tidak bisa menjalankan Pandoc lagi (terjadi di stack dev,
25 November 2026). Pekerja kini mendeteksi ulang alat sekali sebelum menggagalkan tugas
karena "alat tidak tersedia", sehingga deteksi yang gagal sesaat tidak tersimpan
selamanya; restart tetap wajib agar kode baru dipakai.

Produksi memakai Redis database 2 dan node `pekerja1-prod` (bagian 14), sehingga stack
dev boleh tetap berjalan; hentikan stack dev bila tidak dipakai agar tidak berebut CPU.

**Deploy proyek 4 (26 November 2026).** `bash ~/konversiku-dev/app/deploy/deploy_proyek4.sh`
setelah `alat/kirim_vps.sh`: backup kode, unit, dan konfigurasi Nginx ke
`~/konversiku.backup-<waktu>`, hentikan stack dev, rsync kode ke `~/konversiku`, pasang unit
pekerja baru (`MemoryMax=5G`, `TimeoutStopSec=720`), restart `konversiku` dan
`konversiku-pekerja`, pasang konfigurasi Nginx (dipulihkan otomatis bila `nginx -t`
gagal), reload, lalu uji asap. Tidak ada perubahan skema database. Backup pertama:
`~/konversiku.backup-20261125-182115` (waktu VPS, UTC).

Rollback: `sudo systemctl disable --now konversiku-pekerja konversiku-pemulihan.timer
konversiku-pembersihan.timer`, hentikan `konversiku`, kembalikan folder backup ke
`~/konversiku` dan file unit lama, `sudo systemctl daemon-reload`, lalu
`sudo systemctl start konversiku`. Database `konversiku` dan folder data boleh
dibiarkan.

## 14. Sistem tugas tahap 2

**Alur.** Browser mengunggah ke `POST /api/jobs?source=&target=[&formula_guard=]`.
API menulis body secara streaming ke `incoming/<acak>.part` (batas format sumber, 413
bila lewat), membuat kode, lalu dalam satu langkah memindahkan file ke `jobs/<uuid>/input`
dan menyimpan baris `queued` di PostgreSQL. Setelah commit, id tugas dikirim ke
Celery lewat Redis. Balasan 202 `{"kode": ..., "status": "queued"}`; kode hanya
dikirim sekali ini. Pekerja mengambil tugas lewat klaim atomik di PostgreSQL,
menjalankan mesin tahap 1, menulis `result` secara atomik, menandai `success`, lalu
menghapus input.

| Endpoint | Perilaku |
| --- | --- |
| `GET /api/jobs/status` (header `X-Kode-Tugas`) | `status`, `source`, `target`, `dibuat`, `selesai`, `kedaluwarsa`, `pesan`, `catatan`, `posisi_antrean`. 400 kode tidak valid, 404 tidak dikenal |
| `GET /api/jobs/hasil` (header `X-Kode-Tugas`) | File `hasil.<format>` sebagai attachment. 409 belum selesai atau gagal (dengan pesan), 410 kedaluwarsa |
| `GET /healthz` | 200 bila PostgreSQL, Redis, dan folder data sehat; 503 dengan komponen yang gagal |
| `GET /metrics` | Format Prometheus: `konversiku_tugas{status=...}` (tugas yang ada sekarang), `konversiku_tugas_selesai_total{hasil="success"\|"failed"}` (total sepanjang waktu, tabel `job_totals`, tidak berkurang oleh retensi), dan `konversiku_antrean_redis`. Catatan: saat antrean menumpuk, pemulihan mengirim ulang id tugas yang menunggu lebih dari 2 menit, jadi angka antrean Redis bisa lebih besar dari jumlah tugas `queued` |

**Status:** `queued`, `processing`, `success`, `failed`, `expired`.

**Kegagalan dan retry.** File tidak valid, timeout alat, alat tidak ada, atau heap
Pandoc habis langsung `failed` dengan pesan mesin (akan gagal lagi bila diulang).
Kegagalan di luar file (exception tak terduga, batas waktu Celery, pekerja mati)
diulang sampai 3 percobaan, lalu `failed` dengan pesan umum.

**Pemulihan** (`python -m konversiku.recovery`, tiap 1 menit, dan otomatis saat node
pekerja mulai): tugas `processing` yang lease-nya habis atau milik node yang baru
mulai dikembalikan ke antrean; tugas `queued` yang tidak diambil 2 menit dikirim
ulang. Pengiriman ganda aman karena klaim atomik.

**Retensi** (`python -m konversiku.cleanup`, tiap 10 menit): input dihapus begitu
tugas selesai atau gagal. Hasil dan tugas gagal dihapus 24 jam setelah selesai
(bukan setelah unggah) dan statusnya menjadi `expired`; baris `expired` (tanpa isi
dan nama file) dihapus 7 hari kemudian. Unggahan parsial dan folder tanpa baris
dihapus setelah 1 jam.

**Konfigurasi** (variabel lingkungan, bawaan untuk dev):

| Variabel | Bawaan |
| --- | --- |
| `KONVERSIKU_DATA_DIR` | `~/konversiku-dev/data` |
| `KONVERSIKU_DATABASE_URL` | `postgresql:///konversiku_dev` (socket lokal) |
| `KONVERSIKU_DB_SCHEMA` | `public` |
| `KONVERSIKU_REDIS_URL` | `redis://127.0.0.1:6379/0` (tanpa password) |
| `KONVERSIKU_REDIS_PASSWORD_FILE` | `~/konversiku-dev/.redis_password` bila ada |

**Pemisahan lingkungan di satu server Redis** (diuji `tests/test_deploy_config.py`):
dev memakai Redis database 0, tes database 1, produksi database 2
(`konversiku.env.contoh`). Nama node pekerja juga berbeda: dev `pekerja1@<host>`,
produksi `pekerja1-prod@<host>`. Bila produksi memakai database yang sama dengan dev,
pekerja dev mengambil id tugas produksi, menganggapnya "skipped" karena barisnya ada di
database lain, dan tugas produksi tertunda sampai pemulihan.

**Stack dev di VPS** (`~/konversiku-dev`, port 8001): `bash deploy/dev/dev.sh
mulai|henti|status` menjalankan API, pekerja `pekerja1@<host>`, dan loop berkala di
tmux; `bash deploy/dev/uji_asap.sh` menguji submit, status, dan unduh lewat HTTP.
`henti` menghentikan pekerja dengan SIGTERM lewat pidfile lalu menunggu sampai keluar
sebelum menutup tmux. Bila tmux ditutup lebih dulu, Celery menerima SIGHUP dan
menjalankan dirinya ulang di luar tmux sebagai pekerja tersembunyi (terjadi
25 November 2026).
Dari laptop, kode dikirim dengan `alat/kirim_vps.sh` (stack dev otomatis
di-restart) dan tes dijalankan dengan `alat/uji_vps.sh`.

## 15. Pemantauan: Prometheus, node_exporter, Grafana

Terpasang 25 November 2026 (konfigurasi di `deploy/pemantauan/`, skrip
`pasang_pemantauan.sh` dan `pasang_grafana_deb.sh`). **Semua komponen hanya mendengarkan
localhost VPS**; yang terbuka ke jaringan kampus hanya dasbor lewat Nginx.

| Komponen | Versi | Alamat | Catatan |
| --- | --- | --- | --- |
| Prometheus | dari apt Ubuntu 24.04 | 127.0.0.1:9090 | Mengambil `/metrics` KonversiKu, node_exporter, dan dirinya tiap 15 detik; data disimpan 15 hari (`/etc/default/prometheus`, `/etc/prometheus/prometheus.yml`) |
| node_exporter | dari apt Ubuntu 24.04 | 127.0.0.1:9100 | CPU, RAM, disk VPS |
| Grafana | 13.3.2 (`.deb` resmi) | 127.0.0.1:3000, dibuka di **`http://10.33.109.96/grafana/`** | Datasource dan dasbor "KonversiKu" di-provision (tidak bisa diubah dari UI) |

Dasbor berisi: tugas per status, total berhasil dan gagal sepanjang waktu, status API,
CPU, RAM, disk `/`, antrean Redis, riwayat tugas per status, dan tugas selesai per jam.

**Keamanan Grafana**
- Pengunjung dari jaringan melihat dasbor **tanpa login, hanya baca** (Viewer anonim).
- Halaman login `/grafana/login` **diblokir dari jaringan** oleh Nginx (403), dan login API
  basic auth dimatikan (`GF_AUTH_BASIC_ENABLED=false`), sehingga akun admin bawaan tidak
  bisa dipakai dari jaringan.
- **Ganti password admin bawaan Grafana segera setelah pemasangan** (dikerjakan pemilik):
  buka tunnel SSH ke port 3000 VPS (`ssh -N -L 3000:127.0.0.1:3000 <user>@<VPS>`), buka
  `http://127.0.0.1:3000/grafana/login`, masuk dengan akun admin bawaan, lalu Grafana
  meminta password baru.
- Grafana tidak mengirim statistik pemakaian atau mengecek pembaruan ke luar.

**Catatan pemasangan**
- `apt.grafana.com` dan `dl.grafana.com` **tidak bisa dijangkau dari VPS** (diblokir jaringan
  kampus). Paket `grafana_13.3.2_44555861735_linux_amd64.deb` (401,7 MB, SHA-256
  `ab62d8122404cc7510778d4f1c8cd7e8414b95f781a7a3d13f89f07ebb9382a6`) diunduh di laptop dari
  repositori resmi, dicek SHA-256, dikirim ke VPS dalam potongan 50 MB, dicek ulang, lalu
  dipasang dengan `dpkg -i`. Akibatnya Grafana **tidak ter-update otomatis**; pembaruan
  diulang dengan cara yang sama (`pasang_grafana_deb.sh <deb> <sha256>`).
- Setelah dipasang dari apt, Prometheus dan node_exporter sempat mendengarkan semua alamat
  (port 9090/9100) sekitar 15 menit sebelum dibatasi ke localhost. Seri data dari masa itu
  memakai label `localhost:9100` dan tampil sebagai garis kedua yang pendek di grafik selama
  jendela 6 jam.
