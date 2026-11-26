#!/usr/bin/env bash
# Pasang Prometheus, node_exporter, dan Grafana untuk KonversiKu (izin pemilik 25 November 2026).
# Semua hanya di localhost; Grafana dibuka dari jaringan lewat Nginx di /grafana/ (hanya baca).
set -euo pipefail
SRC="$HOME/konversiku-dev/unduhan/pemantauan"
BK="$HOME/konversiku-dev/unduhan/backup-pemantauan-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$BK"

echo "== 1. prometheus dan node_exporter (apt Ubuntu)"
sudo -n DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y -q \
  prometheus prometheus-node-exporter

echo "== 2. repositori resmi Grafana dan paket grafana"
sudo -n install -d -m 755 /etc/apt/keyrings
curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor | sudo -n tee /etc/apt/keyrings/grafana.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
  | sudo -n tee /etc/apt/sources.list.d/grafana.list > /dev/null
sudo -n apt-get -o DPkg::Lock::Timeout=600 update -q
sudo -n DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y -q grafana

echo "== 3. konfigurasi (backup dulu ke $BK)"
for f in /etc/default/prometheus /etc/default/prometheus-node-exporter /etc/prometheus/prometheus.yml \
         /etc/nginx/sites-available/konversiku; do
  sudo -n cp -a "$f" "$BK/" 2>/dev/null || true
done
sudo -n install -m 644 "$SRC/default-prometheus" /etc/default/prometheus
sudo -n install -m 644 "$SRC/default-prometheus-node-exporter" /etc/default/prometheus-node-exporter
sudo -n install -m 644 "$SRC/prometheus.yml" /etc/prometheus/prometheus.yml
promtool check config /etc/prometheus/prometheus.yml
sudo -n install -d -m 755 /etc/systemd/system/grafana-server.service.d
sudo -n install -m 644 "$SRC/grafana-konversiku.conf" /etc/systemd/system/grafana-server.service.d/konversiku.conf
sudo -n install -m 644 "$SRC/grafana-datasource.yaml" /etc/grafana/provisioning/datasources/konversiku.yaml
sudo -n install -m 644 "$SRC/grafana-dashboard-provider.yaml" /etc/grafana/provisioning/dashboards/konversiku.yaml
sudo -n install -d -o grafana -g grafana -m 755 /var/lib/grafana/dashboards
sudo -n install -m 644 -o grafana -g grafana "$SRC/konversiku.json" /var/lib/grafana/dashboards/konversiku.json

echo "== 4. jalankan layanan pemantauan"
sudo -n systemctl daemon-reload
sudo -n systemctl enable prometheus prometheus-node-exporter grafana-server
sudo -n systemctl restart prometheus prometheus-node-exporter grafana-server

echo "== 5. Nginx /grafana/"
sudo -n install -m 644 "$SRC/nginx/konversiku-grafana.conf" /etc/nginx/konversiku-grafana.conf
sudo -n install -m 644 "$SRC/nginx/konversiku.conf" /etc/nginx/sites-available/konversiku
sudo -n nginx -t
sudo -n systemctl reload nginx

echo "== 6. cek"
for _ in $(seq 1 45); do curl -sf http://127.0.0.1:3000/grafana/api/health > /dev/null && break; sleep 2; done
curl -s http://127.0.0.1:3000/grafana/api/health; echo
sleep 20
curl -s http://127.0.0.1:9090/api/v1/targets | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print('target:', [(t['labels']['job'], t['health']) for t in d['data']['activeTargets']])"
ss -ltn | grep -E ":(9090|9100|3000) " || true
systemctl is-active prometheus prometheus-node-exporter grafana-server nginx konversiku konversiku-pekerja
echo "selesai exit=0"
