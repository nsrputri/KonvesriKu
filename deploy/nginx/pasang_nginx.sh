#!/usr/bin/env bash
# Pasang Nginx untuk KonversiKu (izin pemilik 25 November 2026: "Ya, lewat IP").
set -euo pipefail
SRC="$HOME/konversiku-dev/unduhan/nginx"
echo "== 1. apt-get install nginx"
sudo -n DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y -q nginx
echo "== 2. pasang konfigurasi"
sudo -n install -m 644 "$SRC/konversiku-proxy.conf" /etc/nginx/konversiku-proxy.conf
sudo -n install -m 644 "$SRC/konversiku.conf" /etc/nginx/sites-available/konversiku
sudo -n ln -sf /etc/nginx/sites-available/konversiku /etc/nginx/sites-enabled/konversiku
sudo -n rm -f /etc/nginx/sites-enabled/default
echo "== 3. uji dan muat ulang"
sudo -n nginx -t
sudo -n systemctl enable nginx
sudo -n systemctl reload nginx
echo "== 4. cek lokal"
systemctl is-active nginx
curl -s -o /dev/null -w "lokal /: HTTP %{http_code}\n" http://127.0.0.1/
curl -s -w "\n" http://127.0.0.1/healthz
ss -ltn | grep -E ":80 " || true
echo "selesai exit=0"
