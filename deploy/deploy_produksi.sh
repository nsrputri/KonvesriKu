#!/usr/bin/env bash
# Deploy KonversiKu tahap 2 ke ~/konversiku sebagai satu-satunya layanan (izin pemilik 25 November 2026).
# Prototipe lama di ~/konversiku dan unit lama di-backup utuh dulu.
set -euo pipefail
TS=$(date -u +%Y%m%d-%H%M%S)
BK="$HOME/konversiku.backup-$TS"
APP="$HOME/konversiku"

echo "== 1. backup ke $BK"
cp -a "$APP" "$BK"
sudo -n cp -a /etc/systemd/system/konversiku.service "$BK/konversiku.service.lama"
echo "backup: $(find "$BK" -path "$BK/.venv" -prune -o -type f -print | wc -l) file (tanpa .venv) + unit lama"

echo "== 2. hentikan stack dev (port 8001)"
bash "$HOME/konversiku-dev/app/deploy/dev/dev.sh" henti

echo "== 3. salin kode tahap 2 (venv lama dipertahankan)"
rsync -a --delete --exclude '.venv' --exclude 'konversiku.env' --exclude '.redis_password' \
  --exclude '__pycache__' --exclude '.pytest_cache' "$HOME/konversiku-dev/app/" "$APP/"

echo "== 4. dependency"
"$APP/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"

echo "== 5. database, folder data, konfigurasi"
cd /tmp
if ! sudo -n -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='konversiku'" | grep -q 1; then
  sudo -n -u postgres createdb -O b2-3 konversiku
fi
mkdir -p "$HOME/konversiku-data" && chmod 700 "$HOME/konversiku-data"
install -m 600 "$HOME/konversiku-dev/.redis_password" "$APP/.redis_password"
install -m 600 "$APP/deploy/systemd/konversiku.env.contoh" "$APP/konversiku.env"
(set -a; . "$APP/konversiku.env"; set +a; cd "$APP" && .venv/bin/python -c \
  "from konversiku import db, settings; c = db.connect(settings.load_settings()); db.install_schema(c); c.close()")

echo "== 6. pasang unit dan jalankan"
sudo -n install -m 644 "$APP"/deploy/systemd/konversiku*.service "$APP"/deploy/systemd/konversiku*.timer /etc/systemd/system/
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now konversiku konversiku-pekerja konversiku-pemulihan.timer konversiku-pembersihan.timer

echo "== 7. verifikasi"
curl -s --retry 30 --retry-connrefused --retry-delay 1 http://127.0.0.1:8000/healthz; echo
bash "$APP/deploy/dev/uji_asap.sh" http://127.0.0.1:8000
systemctl is-active konversiku konversiku-pekerja konversiku-pemulihan.timer konversiku-pembersihan.timer
echo "selesai exit=0"
