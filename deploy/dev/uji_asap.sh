#!/usr/bin/env bash
# Uji asap stack tahap 2 lewat HTTP sungguhan: submit CSV -> JSON, tunggu success, unduh, cek isi.
# Kode tugas tidak dicetak. Pemakaian: bash uji_asap.sh [URL dasar, bawaan http://127.0.0.1:8001]
set -euo pipefail
BASE="${1:-http://127.0.0.1:8001}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
printf 'nama,nilai\nAni,1\nBudi,2\n' > "$TMP/in.csv"

curl -s --retry 10 --retry-connrefused --retry-delay 1 -o /dev/null "$BASE/api/formats"
curl -s -o "$TMP/submit.json" -w "%{http_code}" -X POST --data-binary @"$TMP/in.csv" \
  -H 'Content-Type: application/octet-stream' "$BASE/api/jobs?source=csv&target=json" > "$TMP/code"
echo "submit: HTTP $(cat "$TMP/code")"
KODE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["kode"])' "$TMP/submit.json")

STATUS=""
for _ in $(seq 1 60); do
  STATUS=$(curl -s -H "X-Kode-Tugas: $KODE" "$BASE/api/jobs/status" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  [ "$STATUS" = "success" ] || [ "$STATUS" = "failed" ] && break
  sleep 1
done
echo "status akhir: $STATUS"
curl -s -o "$TMP/hasil.json" -w "unduh: HTTP %{http_code}\n" -H "X-Kode-Tugas: $KODE" "$BASE/api/jobs/hasil"
python3 - "$TMP/hasil.json" <<'EOF'
import json, sys
data = json.load(open(sys.argv[1]))
assert data == [{"nama": "Ani", "nilai": "1"}, {"nama": "Budi", "nilai": "2"}] or \
       data == [{"nama": "Ani", "nilai": 1}, {"nama": "Budi", "nilai": 2}], data
print("isi hasil: benar")
EOF
[ "$STATUS" = "success" ]
