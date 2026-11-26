"""Endpoint sinkron tahap 1 (/api/convert) hanya ada bila KONVERSIKU_SYNC_ENDPOINT=1 (tes)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = """
from fastapi.testclient import TestClient
import app
with TestClient(app.app) as client:
    r = client.post('/api/convert', params={'source': 'csv', 'target': 'json'},
                    content=b'a,b\\n1,2\\n', headers={'Content-Type': 'application/octet-stream'})
    print(r.status_code)
"""


def _status(sync: str | None) -> int:
    env = {k: v for k, v in os.environ.items() if k != "KONVERSIKU_SYNC_ENDPOINT"}
    if sync is not None:
        env["KONVERSIKU_SYNC_ENDPOINT"] = sync
    done = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return int(done.stdout.strip().splitlines()[-1])


def test_production_app_has_no_sync_endpoint():
    assert _status(None) in (404, 405)


def test_sync_endpoint_only_with_flag():
    assert _status("1") == 200
