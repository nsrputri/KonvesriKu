# STATUS_PRD: KonversiKu setelah tahap 2

**Acuan:** `PRD.md` di folder kerja, yaitu PRD dengan pembaruan 19 November 2026
yang menambahkan kebutuhan praktikum AMJ (aplikasi server, DNS Bind9, VPS kampus,
pemantauan). `PRD.txt` adalah versi sebelum pembaruan dan disimpan untuk riwayat.

Paket ini adalah **tahap 2**: mesin konversi tahap 1 (72 pasangan) ditambah sistem
tugas cloud: unggahan streaming ke disk, kode tugas, antrean PostgreSQL + Redis
(Celery), pekerja terpisah, status, unduh lintas sesi, retensi 24 jam, pemulihan,
`/healthz`, dan `/metrics`. Sejak proyek 4 (26 November 2026) batas unggahan per format
2 sampai 25 MiB hasil pengukuran di VPS. PRD **belum selesai**: target 1 GB belum, dan
infrastruktur AMJ (Nginx, Bind9, Prometheus/Grafana, HTTPS, ufw/fail2ban) serta
deploy ke layanan produksi sudah dilakukan: tahap 2 menjadi satu-satunya layanan KonversiKu
di VPS (port 8000) sejak 25 November 2026.

Bukti tes (output asli di `laporan_tes/`, 25 November 2026):
- `tahap2_vps.txt`: VPS Ubuntu 24.04.5, Python 3.12.3, PostgreSQL 16.15, Redis 7.0.15,
  Pandoc 3.13, LibreOffice 24.2.7: **295 passed, 0 failed, 0 skipped**.
- `tahap2_laptop.txt`: Windows 11, Python 3.11.9: **235 passed, 60 skipped** (tes yang
  butuh PostgreSQL/Redis; semuanya lulus di VPS), 0 failed.
- `tahap2_e2e.txt`: Chrome di laptop ke stack dev di VPS lewat tunnel: **19 passed**.
- `pytest_output.txt`: hasil tahap 1 (228 passed) sebagai riwayat.

Bukti di atas berasal dari stack dev (`~/konversiku-dev`, port 8001). Setelah deploy,
layanan produksi diverifikasi terpisah: `/healthz` ok, uji asap HTTP lulus, dan 19 tes browser
lulus terhadap port 8000 (`tahap2_e2e_produksi.txt`). Stack dev dimatikan; di VPS hanya
ada satu layanan KonversiKu.

Status: **Sudah** = diimplementasikan dan diuji; **Sebagian** = sebagian ada atau
dengan batas; **Belum** = belum ada.

## 1. Ringkasan Produk

| Butir PRD | Status | Bukti / batas aktual |
| --- | --- | --- |
| Konversi file berbasis teks di sisi server | Sudah | 72 pasangan, `tests/test_documents.py`, `tests/test_data.py`; di VPS dikerjakan pekerja Celery |
| Fokus file besar yang lama dikonversi | Sebagian | Batas per format 2 sampai 25 MiB (proyek 4), diukur di VPS: file sebesar batas selesai <= 300 detik dengan beban memori seluruh pohon proses <= 1.500 MiB, sekitar 1,57 GB (`laporan_tes/kapasitas_proyek4_verifikasi_pohon.txt`, 72 pasangan dan bentuk padat). 1 GB belum |
| Tetap lanjut walau koneksi putus setelah unggahan | Sudah | Konversi di pekerja terpisah; E2E `test_close_browser_then_download_with_code_in_new_context`; `test_api_restart_keeps_status` |
| Hasil bisa diunduh di sesi berikutnya | Sudah | Kode tugas lewat header; E2E unduh dari context browser baru |
| Data dihapus otomatis dalam 24 jam | Sudah | Input dihapus begitu selesai/gagal; hasil 24 jam setelah selesai; `tests/tahap2/test_cleanup.py` (jam palsu) |
| Proyek gabungan Cloud Computing dan praktikum AMJ | Sebagian | Aplikasi server baru di VPS: PostgreSQL, Redis (dipakai tahap 2). Bind9, Nginx, pemantauan belum (Bagian 8, 12) |

## 2. Latar Belakang dan 3. Target Pengguna

Konteks, tanpa kriteria teknis. "Proses tetap berjalan walau terputus", "unduh di
sesi berikutnya", dan "jaminan file tidak disimpan terlalu lama" kini terpenuhi
untuk file sampai batas formatnya (TXT 2 MiB, MD/RTF/HTML 4 MiB, RST/TEX 3 MiB, DOCX/PDF
20 MiB, data 25 MiB, YAML 10 MiB). "File besar" dalam arti 1 GB belum.

## 4. Tujuan dan Indikator

| Tujuan | Target PRD | Status | Bukti / batas aktual |
| --- | --- | --- | --- |
| Mudah diakses | Maks. 3 langkah sampai konversi dimulai | Sudah | Pilih/tarik file, pilih format, klik Konversi. E2E `test_csv_picker_json_then_tsv` |
| File besar | Hingga 1 GB | Sebagian | Batas per format: TXT 2 MiB, MD/RTF/HTML 4 MiB, RST/TEX 3 MiB, DOCX/PDF 20 MiB plus batas isi, CSV/TSV/JSON/XML 25 MiB, YAML 10 MiB. Bukti produksi: CSV tepat 25 MiB lewat Nginx selesai dan benar (`laporan_tes/proyek4_bukti_produksi.txt`); dua tugas terberat bersamaan, puncak pekerja 2,61 GB (`laporan_tes/proyek4_bersamaan_produksi.txt`). 1 GB butuh mesin streaming, belum |
| Tahan terputus | 100% tugas selesai | Sudah (diuji) | Pekerja dibunuh `SIGKILL` di tengah tugas lalu tugas selesai (`test_killed_worker_job_finishes_after_restart`, uji mutasi membuktikan tes menangkap kegagalan); pesan antrean hilang dikirim ulang (`test_stale_queued_resent_after_two_minutes`). Reboot VPS tidak diuji (server bersama) |
| Format beragam | Minimal 5 format teks | Sudah | 13 format |
| Data aman | Hasil terhapus 24 jam setelah siap | Sudah | `test_result_removed_24h_after_finish_not_upload`; timer pembersihan aktif di VPS (tiap 10 menit) |
| Nama domain | Domain diselesaikan Bind9, diuji `dig` | Belum | Proyek berikutnya |
| Terpantau | Dasbor Grafana aktif | Sudah | Dasbor "KonversiKu" di `http://10.33.109.96/grafana/` (hanya baca, tanpa login): tugas per status, total berhasil/gagal, status API, CPU, RAM, disk, antrean Redis. Prometheus mengambil `/metrics` tiap 15 detik (25 November 2026) |
| Aplikasi server (AMJ) | Bind9 + minimal 2 aplikasi baru | Sebagian | PostgreSQL dan Redis terpasang dan dipakai; Bind9 belum |

## 5. Fitur Utama (MVP)

| Fitur | Status | Bukti / batas aktual |
| --- | --- | --- |
| Unggah file besar | Sebagian | Unggahan streaming ke `incoming/`, berhenti saat lewat batas, `.part` dihapus bila gagal atau putus (`test_aborted_upload_leaves_no_files_or_rows`). Batas per format sampai 25 MiB; unggahan 25 MiB lewat VPN 9 sampai 11 detik |
| Pilih format tujuan | Sudah | Pemilih radial, pagination, alasan pasangan nonaktif; E2E |
| Konversi di server | Sudah | Antrean Celery + pekerja terpisah; klaim atomik (`test_concurrent_claims_only_one_wins`, `test_two_workers_never_process_same_job`) |
| Status dan kode tugas | Sudah | `queued/processing/success/failed/expired`, kode `token_urlsafe(32)`, posisi antrean; `tests/tahap2/test_jobs_api.py` |
| Hapus otomatis | Sudah | `tests/tahap2/test_cleanup.py` |
| Akses lewat nama domain (Bind9, Nginx) | Sebagian | Nginx sudah: layanan dibuka di `http://10.33.109.96` dari jaringan kampus/VPN (19 tes browser lulus, `tahap2_e2e_nginx.txt`). Nama domain (Bind9) dan HTTPS belum |
| Pemantauan layanan | Sudah | `/healthz`, `/metrics` (jumlah tugas per status, total berhasil dan gagal sepanjang waktu, panjang antrean Redis; `test_metrics_totals_survive_expiry_and_row_deletion`); Prometheus 2, node_exporter, dan Grafana 13.3.2 terpasang dan berjalan |
| Unggah dapat dilanjutkan, audio/video, notifikasi, MinIO | Belum (versi berikutnya menurut PRD) | Tidak dikerjakan |

## 6. Format Input dan Output

Tidak berubah dari tahap 1; semua pasangan kini berjalan lewat pekerja.

| Butir | Status | Bukti / batas aktual |
| --- | --- | --- |
| Dokumen teks D ke D lain, DOCX, PDF | Sudah | 42 pasangan, `test_document_pair_converts_and_output_parses` (lulus di Windows dan Ubuntu) |
| DOCX ke D dan PDF | Sudah | 7 pasangan |
| PDF berbasis teks ke TXT, MD, DOCX | Sudah | 3 pasangan; PDF pindai/terenkripsi/rusak ditolak |
| Data CSV, TSV, JSON, YAML, XML antaranggota | Sudah | 20 pasangan |
| Data ke dokumen/PDF | Belum (PRD: versi berikutnya) | Ditolak dengan pesan jelas |
| Validasi dari isi | Sebagian | Divalidasi mesin di pekerja; file tidak valid berakhir `failed` dengan pesan. TXT/MD/RST/TeX tidak bisa dibedakan pasti |
| PDF pindai ditolak dengan pesan jelas | Sudah | `test_pdf_without_text_layer_rejected` |
| Gambar tertanam, format hilang, data bersarang, komentar YAML/XML | Sudah (sesuai PRD) | Catatan per pasangan; PETUNJUK bagian 7 |
| "Diproses dengan Pandoc" untuk TXT | Sebagian | Pandoc tidak punya reader teks biasa; TXT di-escape lalu dibaca Pandoc |

Temuan di Ubuntu 24.04: Pandoc dari apt (3.1.3) merusak RTF dan tidak bisa menulis
DOCX di `--sandbox`. VPS memakai Pandoc 3.13 rilis resmi (PETUNJUK bagian 3).

## 7. Kebutuhan Non-Fungsional

| Butir | Status | Bukti / batas aktual |
| --- | --- | --- |
| Keandalan: antrean persisten, lanjut setelah restart | Sudah | Status di PostgreSQL, Redis dengan AOF, pemulihan berkala dan saat pekerja mulai, retry maksimal 3 (`test_unexpected_error_retries_then_fails`). Pekerja yang tugasnya diambil alih tidak menandai sukses (`test_lost_ownership_does_not_mark_success_or_delete_input`); deteksi alat diulang sekali sebelum gagal (`test_tool_detection_retried_once_before_failing`). Produksi memakai antrean Redis terpisah dari dev (`tests/test_deploy_config.py`) |
| Keamanan: HTTPS | Belum | Nginx sudah melayani HTTP di port 80; HTTPS menunggu keputusan domain |
| Keamanan: kode tugas acak panjang | Sudah | 256 bit, hanya SHA-256 di database, lewat header, tidak ada di log (`test_code_not_in_any_log`) |
| Keamanan: validasi jenis dan ukuran | Sudah | Batas per format (413 menyebut format), batas isi DOCX/PDF, batas ZIP/JSON/YAML/XML; `tests/test_limits.py` |
| Retensi data | Sudah | Input segera, hasil/gagal 24 jam, baris expired 7 hari, yatim 1 jam |
| Privasi: tanpa akun, isi file tidak di-log | Sudah | Nama file asli tidak disimpan di server |
| Performa: selesai tuntas, status dapat dipantau | Sudah | Status dan posisi antrean di UI; batas waktu tugas 600/660 detik |
| Ketersediaan 99% | Belum terukur | Butuh pemantauan setelah deploy |
| Skalabilitas: pekerja dapat ditambah, batas tugas bersamaan | Sudah | Node pekerja bernama tetap, konkurensi 2 per node, antrean maks. 50, kuota data 5 GB (`test_queue_full_is_503`, `test_quota_full_is_503`) |
| Keterpantauan: `/healthz`, `/metrics`, dasbor | Sebagian | Endpoint ada; dasbor belum |
| Keamanan jaringan | Sebagian | PostgreSQL dan Redis hanya localhost, Redis berpassword. ufw/fail2ban dan HTTPS belum (butuh izin pemilik) |
| Operasional: skrip setup, backup, rotasi log | Sebagian | Unit systemd dan langkah deploy tertulis (PETUNJUK bagian 13), skrip pemasangan dev; backup basis data dan rotasi log belum |

## 8. Arsitektur dan Teknologi

| Komponen usulan | Status | Catatan |
| --- | --- | --- |
| Python + FastAPI | Sudah | `app.py`, `konversiku/jobs_api.py` |
| Celery dengan Redis, PostgreSQL | Sudah | Pilihan pemilik 25 November 2026 (menggantikan SQLite di INSTRUKSI) |
| Pandoc | Sudah | 3.13 rilis resmi di VPS |
| PyMuPDF untuk PDF | Diganti | pypdfium2 (lisensi; PyMuPDF AGPL) |
| LibreOffice headless | Sudah | 24.2.7 di VPS |
| pandas, PyYAML, xmltodict | Sebagian | pandas diganti `csv` stdlib |
| Cron/penjadwal penghapusan | Sudah | Timer systemd pemulihan (1 menit) dan pembersihan (10 menit) aktif di VPS sejak 25 November 2026 |
| VPS kampus (private cloud) | Sudah | Layanan `konversiku` (API port 8000) dan `konversiku-pekerja` berjalan lewat systemd, aktif saat boot; database `konversiku`, Redis database 2, data di `~/konversiku-data`. Akses hanya lewat VPN kampus |
| Nginx | Sebagian | Terpasang 25 November 2026: port 80, batas unggah, rate limit per IP, `/metrics` dan `/healthz` diblokir dari jaringan. HTTPS belum |
| Bind9 | Belum | Wajib menurut PRD; menunggu keputusan domain dan izin pengelola lab |
| PostgreSQL, Redis | Sudah | Terpasang 25 November 2026, hanya localhost |
| Prometheus, Grafana, node_exporter | Sudah | Terpasang 25 November 2026, semuanya hanya localhost; Grafana dibuka lewat Nginx `/grafana/` (hanya baca; login admin diblokir dari jaringan, basic auth API mati). Grafana dipasang dari `.deb` resmi yang dikirim dari laptop karena grafana.com diblokir dari VPS |
| ufw, fail2ban | Belum | Butuh izin eksplisit pemilik (firewall) |
| MinIO | Belum | Opsional, versi berikutnya |

## 9. Batasan dan Asumsi

| Butir | Status |
| --- | --- |
| MVP hanya file teks, tanpa gambar | Sudah |
| PDF input hanya berbasis teks | Sudah |
| Ukuran dibatasi dan ada batas waktu | Sudah, per format 2 sampai 25 MiB, tugas maks. 10 menit (bukan 1 GB) |
| Konversi dijamin hanya setelah unggahan lengkap | Sudah: tugas dibuat setelah unggahan lengkap; unggahan putus tidak membuat tugas |
| Tanpa akun; hasil lewat kode tugas | Sudah |
| File asli dihapus setelah konversi | Sudah |
| Hasil PDF bisa berbeda dari asli | Sudah dinyatakan |
| VPS kampus dengan IP privat | Belum terpenuhi: hanya lewat VPN; akses publik belum |
| Kapasitas mengikuti sumber daya VPS | Tercatat: 2 inti, RAM 7,8 GiB, disk sisa sekitar 28 GB |
| Asumsi izin pengelola lab | Belum dikonfirmasi |

## 10. Estimasi Biaya dan Jadwal

Tidak relevan untuk kode. Jadwal PRD 7 minggu; demo UAS kurang dari 3 minggu lagi
(per 25 November 2026).

## 11. Risiko dan Mitigasi

| Risiko | Status mitigasi |
| --- | --- |
| Disk penuh | Sudah: batas ukuran, kuota folder data 5 GB, antrean maks. 50, retensi otomatis |
| Tugas gagal/terhenti | Sudah: antrean persisten, retry terbatas, pemulihan, status jelas |
| Konversi lama menghabiskan sumber daya | Sudah: timeout per alat, batas waktu Celery, konkurensi 2 per pekerja |
| Kode tugas hilang/ditebak | Sudah untuk ditebak (256 bit, hanya hash disimpan); kode hilang tetap tidak bisa dipulihkan (tanpa akun, sesuai PRD) |
| File berbahaya | Sudah (tahap 1); isolasi user/container terpisah belum |
| Layanan hanya bisa dibuka dari jaringan kampus | Belum |
| VPS kampus tidak tersedia | Sebagian: langkah deploy dan unit tertulis; backup basis data belum |
| Port basis data/antrean terbuka | Sudah: hanya localhost, Redis berpassword |
| Konfigurasi manual sulit diulang | Sebagian: unit dan contoh konfigurasi; skrip setup penuh belum |

## 12. Pemenuhan Ketentuan Praktikum AMJ

| Ketentuan | Status | Catatan |
| --- | --- | --- |
| Dikerjakan berkelompok | Di luar kode | |
| Minimal 2 aplikasi server terpasang | Sudah | PostgreSQL, Redis, Nginx, Prometheus, node_exporter, dan Grafana terpasang dan dipakai |
| Bind9 wajib, diuji `dig` | Belum | |
| Minimal 2 aplikasi baru | Belum dipastikan | Daftar aplikasi praktikum sebelumnya belum diketahui |
| Presentasi dan demo UAS | Sebagian | Alur kode tugas, putus koneksi, dan retensi sudah bisa didemokan di stack dev; domain, HTTPS, Grafana belum |
| Laporan | Di luar kode | |

## Yang masih belum memenuhi PRD (ringkas)

1. File besar: batas per format 2 sampai 25 MiB sudah (proyek 4); 1 GB butuh mesin streaming.
2. (Selesai 25 November 2026: tahap 2 di-deploy sebagai satu-satunya layanan, port 8000.)
3. Akses lewat nama domain (Bind9) dan HTTPS. Nginx lewat IP sudah jalan.
4. (Selesai 25 November 2026: Prometheus, node_exporter, dan dasbor Grafana.)
5. Keamanan jaringan dan operasional: ufw, fail2ban, backup basis data, rotasi log.
6. Akses publik dari luar jaringan kampus.
7. Uptime 99% belum terukur; reboot VPS tidak diuji.
8. Konversi data ke dokumen/PDF (PRD: versi berikutnya).
