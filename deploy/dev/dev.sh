#!/usr/bin/env bash
# Stack dev KonversiKu tahap 2 di VPS (~/konversiku-dev), dijalankan di tmux:
#   kk-api      uvicorn app:app di 127.0.0.1:8001
#   kk-pekerja  pekerja Celery bernama pekerja1@<host>, antrean "konversiku"
#   kk-berkala  pemulihan tiap 60 detik, pembersihan tiap 10 menit
# Pemakaian: bash dev.sh mulai|henti|status
set -euo pipefail
ROOT="$HOME/konversiku-dev"
APP="$ROOT/app"
BIN="$ROOT/.venv/bin"
LOG="$ROOT/log"
PIDFILE="$ROOT/run/pekerja.pid"
SESSIONS=(kk-api kk-pekerja kk-berkala)

mulai() {
  mkdir -p "$LOG" "$ROOT/data" "$ROOT/run"
  chmod 700 "$ROOT/data"
  (cd "$APP" && "$BIN/python" -c \
    "from konversiku import db, settings; c = db.connect(settings.load_settings()); db.install_schema(c); c.close()")
  for name in "${SESSIONS[@]}"; do
    if tmux has-session -t "$name" 2>/dev/null; then echo "$name sudah berjalan"; return 1; fi
  done
  tmux new-session -d -s kk-api \
    "cd '$APP' && exec '$BIN/python' -m uvicorn app:app --host 127.0.0.1 --port 8001 >> '$LOG/api.log' 2>&1"
  tmux new-session -d -s kk-pekerja \
    "cd '$APP' && exec '$BIN/celery' -A konversiku.celery_app worker -n pekerja1@%h -Q konversiku --loglevel INFO --pidfile '$PIDFILE' >> '$LOG/pekerja.log' 2>&1"
  tmux new-session -d -s kk-berkala \
    "cd '$APP' && n=0; while true; do '$BIN/python' -m konversiku.recovery >> '$LOG/berkala.log' 2>&1; \
     if [ \$((n % 10)) -eq 0 ]; then '$BIN/python' -m konversiku.cleanup >> '$LOG/berkala.log' 2>&1; fi; \
     n=\$((n + 1)); sleep 60; done"
  curl -s --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null http://127.0.0.1:8001/api/formats \
    || echo "peringatan: API belum menjawab setelah 30 detik, lihat $LOG/api.log"
  echo "dimulai: ${SESSIONS[*]}"
}

henti() {
  # Hentikan pekerja dengan SIGTERM lewat pidfile dan tunggu sampai keluar SEBELUM tmux ditutup.
  # Bila tmux ditutup lebih dulu, Celery menerima SIGHUP dan menjalankan dirinya ulang di luar
  # tmux (pekerja tersembunyi yang tetap mengambil tugas dengan kode lama).
  if [ -s "$PIDFILE" ]; then
    local pid
    pid=$(cat "$PIDFILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid"
      for _ in $(seq 1 120); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid"; echo "pekerja dipaksa berhenti (KILL)"; fi
      echo "pekerja berhenti: pid $pid"
    fi
    rm -f "$PIDFILE"
  fi
  for name in "${SESSIONS[@]}"; do
    tmux kill-session -t "$name" 2>/dev/null && echo "dihentikan: $name" || true
  done
}

status() {
  for name in "${SESSIONS[@]}"; do
    if tmux has-session -t "$name" 2>/dev/null; then echo "$name: jalan"; else echo "$name: mati"; fi
  done
  curl -s -m 5 -o /dev/null -w "api /api/formats: HTTP %{http_code}\n" http://127.0.0.1:8001/api/formats || true
  echo "proses pekerja dev: $(pgrep -u "$(id -u)" -fc "^$BIN/python3 $BIN/celery -A konversiku.celery_app worker -n pekerja1@" || true)"
}

case "${1:-}" in
  mulai) mulai ;;
  henti) henti ;;
  status) status ;;
  *) echo "pemakaian: bash dev.sh mulai|henti|status" >&2; exit 2 ;;
esac
