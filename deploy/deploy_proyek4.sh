#!/usr/bin/env bash
# Deploy proyek 4 (batas unggah per format) ke produksi ~/konversiku (rencana proyek 4 Task 4,
# disetujui pemilik 25 November 2026). Kode diambil dari ~/konversiku-dev/app (sudah dikirim
# dengan alat/kirim_vps.sh). Tidak ada perubahan skema database.
# Perintah sudo: install unit pekerja dan konfigurasi Nginx, daemon-reload, restart konversiku dan
# konversiku-pekerja, nginx -t, reload nginx. Bila nginx -t gagal, konfigurasi lama dipulihkan.
set -euo pipefail
TS=$(date -u +%Y%m%d-%H%M%S)
BK="$HOME/konversiku.backup-$TS"
APP="$HOME/konversiku"
SRC="$HOME/konversiku-dev/app"

echo "== 1. backup ke $BK (kode tanpa .venv, unit dan konfigurasi Nginx yang terpasang)"
mkdir -p "$BK"
rsync -a --exclude '.venv' --exclude '__pycache__' "$APP/" "$BK/app/"
cp -a /etc/systemd/system/konversiku.service /etc/systemd/system/konversiku-pekerja.service "$BK/"
cp -a /etc/nginx/sites-available/konversiku "$BK/nginx-konversiku.conf"
cp -a /etc/nginx/konversiku-proxy.conf "$BK/nginx-konversiku-proxy.conf"
echo "backup: $(find "$BK" -type f | wc -l) file"

echo "== 2. hentikan stack dev (port 8001)"
bash "$SRC/deploy/dev/dev.sh" henti || true

echo "== 3. tugas produksi yang sedang berjalan (pekerja menunggu tugas berjalan selesai saat stop)"
(set -a; . "$APP/konversiku.env"; set +a; cd "$APP" && .venv/bin/python -c "
from konversiku import db, settings
c = db.connect(settings.load_settings())
print(c.execute(\"SELECT status, count(*) AS n FROM jobs WHERE status IN ('queued','processing') GROUP BY status\").fetchall())
c.close()")

echo "== 4. salin kode baru (venv, env, dan sandi Redis dipertahankan)"
rsync -a --delete --exclude '.venv' --exclude 'konversiku.env' --exclude '.redis_password' \
  --exclude '__pycache__' --exclude '.pytest_cache' "$SRC/" "$APP/"
"$APP/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"

echo "== 5. unit pekerja (MemoryMax=5G, TimeoutStopSec=720) dan restart"
sudo -n install -m 644 "$APP/deploy/systemd/konversiku-pekerja.service" /etc/systemd/system/
sudo -n systemctl daemon-reload
sudo -n systemctl restart konversiku konversiku-pekerja
systemctl show konversiku-pekerja -p MemoryMax -p TimeoutStopUSec

echo "== 6. Nginx (client_max_body_size 26m, timeout 300 detik)"
sudo -n install -m 644 "$APP/deploy/nginx/konversiku-proxy.conf" /etc/nginx/konversiku-proxy.conf
sudo -n install -m 644 "$APP/deploy/nginx/konversiku.conf" /etc/nginx/sites-available/konversiku
if ! sudo -n nginx -t; then
  echo "nginx -t gagal: konfigurasi lama dipulihkan"
  sudo -n install -m 644 "$BK/nginx-konversiku-proxy.conf" /etc/nginx/konversiku-proxy.conf
  sudo -n install -m 644 "$BK/nginx-konversiku.conf" /etc/nginx/sites-available/konversiku
  sudo -n nginx -t
  exit 1
fi
sudo -n systemctl reload nginx

echo "== 7. verifikasi"
curl -s --retry 30 --retry-connrefused --retry-delay 1 http://127.0.0.1:8000/healthz; echo
bash "$APP/deploy/dev/uji_asap.sh" http://127.0.0.1:8000
curl -s http://127.0.0.1/api/formats | "$APP/.venv/bin/python" -c \
  "import json,sys; l=json.load(sys.stdin)['limits']; print('lewat Nginx:', l['max_upload_bytes'], l['max_upload_bytes_per_source'])"
systemctl is-active konversiku konversiku-pekerja nginx konversiku-pemulihan.timer konversiku-pembersihan.timer
echo "selesai exit=0"
