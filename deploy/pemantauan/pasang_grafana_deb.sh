#!/usr/bin/env bash
# Pasang Grafana dari file .deb resmi yang dikirim dari laptop (VPS tidak bisa menjangkau
# grafana.com dari jaringan kampus). Izin pemilik 25 November 2026.
# Pemakaian: bash pasang_grafana_deb.sh <path .deb> <sha256 resmi>
set -euo pipefail
DEB="$1"
SHA="$2"
SRC="$HOME/konversiku-dev/unduhan/pemantauan"
BK="$HOME/konversiku-dev/unduhan/backup-pemantauan-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$BK"

echo "== 1. cek SHA-256"
echo "$SHA  $DEB" | sha256sum -c -

echo "== 2. dpkg -i grafana"
sudo -n DEBIAN_FRONTEND=noninteractive dpkg -i "$DEB" \
  || sudo -n DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 -f install -y -q

echo "== 3. konfigurasi Grafana (hanya localhost, dasbor hanya baca)"
sudo -n install -d -m 755 /etc/systemd/system/grafana-server.service.d
sudo -n install -m 644 "$SRC/grafana-konversiku.conf" /etc/systemd/system/grafana-server.service.d/konversiku.conf
sudo -n install -m 644 "$SRC/grafana-datasource.yaml" /etc/grafana/provisioning/datasources/konversiku.yaml
sudo -n install -m 644 "$SRC/grafana-dashboard-provider.yaml" /etc/grafana/provisioning/dashboards/konversiku.yaml
sudo -n install -d -o grafana -g grafana -m 755 /var/lib/grafana/dashboards
sudo -n install -m 644 -o grafana -g grafana "$SRC/konversiku.json" /var/lib/grafana/dashboards/konversiku.json

echo "== 4. jalankan Grafana"
sudo -n systemctl daemon-reload
sudo -n systemctl enable grafana-server
sudo -n systemctl restart grafana-server

echo "== 5. Nginx /grafana/ (backup konfigurasi lama dulu)"
sudo -n cp -a /etc/nginx/sites-available/konversiku "$BK/nginx-konversiku.conf"
sudo -n install -m 644 "$SRC/nginx/konversiku-grafana.conf" /etc/nginx/konversiku-grafana.conf
sudo -n install -m 644 "$SRC/nginx/konversiku.conf" /etc/nginx/sites-available/konversiku
sudo -n nginx -t
sudo -n systemctl reload nginx

echo "== 6. cek"
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:3000/grafana/api/health > /dev/null && break; sleep 2; done
curl -s http://127.0.0.1:3000/grafana/api/health; echo
ss -ltn | grep -E ":(9090|9100|3000) " || true
systemctl is-active grafana-server prometheus prometheus-node-exporter nginx konversiku konversiku-pekerja
echo "selesai exit=0"
