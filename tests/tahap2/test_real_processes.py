"""Uji proses sungguhan: uvicorn + pekerja Celery + PostgreSQL + Redis (hanya di VPS/Linux).

- uvicorn memakai socket UNIX (tanpa port TCP tambahan; batas izin dev hanya port 8001).
- Schema PostgreSQL uji dan Redis database 1, terpisah dari stack dev.
- ``KONVERSIKU_MODE_UJI=1`` + ``KONVERSIKU_UJI_JEDA_DETIK`` membuat pekerja menahan tugas
  sebentar setelah klaim agar bisa dibunuh di tengah pekerjaan.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import RECORDS, data_sample, records_of

pytestmark = [
    pytest.mark.layanan,
    pytest.mark.proses,
    pytest.mark.skipif(sys.platform == "win32", reason="butuh socket UNIX dan grup proses POSIX"),
]

APP_DIR = Path(__file__).resolve().parents[2]
HEADER = "X-Kode-Tugas"


class Proc:
    def __init__(self, args, env, log_path: Path):
        self.log_path = log_path
        self.log = open(log_path, "ab")
        self.popen = subprocess.Popen(args, cwd=APP_DIR, env=env, stdout=self.log, stderr=subprocess.STDOUT,
                                      start_new_session=True)

    def text(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def signal(self, sig) -> None:
        try:
            os.killpg(self.popen.pid, sig)
        except ProcessLookupError:
            pass

    def kill(self) -> None:
        self.signal(signal.SIGKILL)
        self.popen.wait(30)

    def stop(self) -> None:
        if self.popen.poll() is None:
            self.signal(signal.SIGTERM)
            try:
                self.popen.wait(30)
            except subprocess.TimeoutExpired:
                self.kill()
        self.log.close()


def _wait(predicate, timeout: float, what: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.3)
    raise AssertionError(f"menunggu {what} lebih dari {timeout} detik")


class Stack:
    def __init__(self, settings, tmp_path: Path):
        self.settings = settings
        self.dir = tmp_path
        self.procs: list[Proc] = []
        self.socket = tmp_path / "api.sock"

    def env(self, delay: float) -> dict:
        env = {**os.environ,
               "KONVERSIKU_DATA_DIR": str(self.settings.data_dir),
               "KONVERSIKU_DB_SCHEMA": self.settings.db_schema,
               "KONVERSIKU_DATABASE_URL": self.settings.database_url,
               "KONVERSIKU_REDIS_URL": self.settings.redis_url,
               "KONVERSIKU_MODE_UJI": "1",
               "KONVERSIKU_UJI_JEDA_DETIK": str(delay),
               "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("KONVERSIKU_SYNC_ENDPOINT", None)
        if self.settings.redis_password_file:
            env["KONVERSIKU_REDIS_PASSWORD_FILE"] = str(self.settings.redis_password_file)
        return env

    def api(self) -> Proc:
        self.socket.unlink(missing_ok=True)
        proc = Proc([sys.executable, "-m", "uvicorn", "app:app", "--uds", str(self.socket)],
                    self.env(0), self.dir / "api.log")
        self.procs.append(proc)
        _wait(lambda: self._ready(), 60, "uvicorn siap")
        return proc

    def _ready(self) -> bool:
        try:
            return self.client().get("/api/formats").status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def client(self):
        import httpx

        return httpx.Client(transport=httpx.HTTPTransport(uds=str(self.socket)), base_url="http://uji",
                            timeout=30)

    def worker(self, name: str, delay: float = 8, log_name: str | None = None) -> Proc:
        log = self.dir / (log_name or f"{name.split('@')[0]}.log")
        start = log.stat().st_size if log.exists() else 0
        proc = Proc([sys.executable, "-m", "celery", "-A", "konversiku.celery_app", "worker", "-n", name,
                     "-Q", "konversiku", "-c", "2", "--loglevel", "INFO", "--without-gossip",
                     "--without-mingle", "--without-heartbeat"], self.env(delay), log)
        self.procs.append(proc)
        _wait(lambda: " ready." in log.read_text(errors="replace")[start:], 60, f"pekerja {name} siap")
        return proc

    def close(self) -> None:
        for proc in reversed(self.procs):
            proc.stop()


@pytest.fixture()
def stack(conn, settings, tmp_path):
    import redis

    from konversiku.settings import broker_url

    client = redis.Redis.from_url(broker_url(settings))
    client.delete("konversiku", "unacked", "unacked_index")
    created = Stack(settings, tmp_path)
    yield created
    created.close()
    client.delete("konversiku", "unacked", "unacked_index")
    client.close()


def _submit(api_client) -> str:
    response = api_client.post("/api/jobs", params={"source": "csv", "target": "json"},
                               content=data_sample("csv"), headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 202, response.text
    return response.json()["kode"]


def _status(api_client, kode: str) -> str:
    return api_client.get("/api/jobs/status", headers={HEADER: kode}).json()["status"]


def _job(conn, kode):
    from konversiku import jobs

    job = jobs.find_by_code_hash(conn, jobs.hash_code(kode))
    conn.commit()
    return job


def test_killed_worker_job_finishes_after_restart(stack, conn):
    stack.api()
    first = stack.worker("pekerja1@uji", delay=8)
    with stack.client() as client:
        kode = _submit(client)
        _wait(lambda: _status(client, kode) == "processing", 60, "tugas diproses")
        first.kill()                                   # pekerja mati di tengah tugas
        assert _status(client, kode) == "processing"
        stack.worker("pekerja1@uji", delay=1, log_name="pekerja1-ulang.log")   # nama node sama
        _wait(lambda: _status(client, kode) == "success", 90, "tugas selesai setelah pemulihan")
        result = client.get("/api/jobs/hasil", headers={HEADER: kode})
    assert result.status_code == 200
    assert records_of("json", result.content) == RECORDS
    assert _job(conn, kode).attempts == 2


def test_api_restart_keeps_status(stack):
    api = stack.api()
    with stack.client() as client:
        kode = _submit(client)                         # belum ada pekerja: tetap queued
        before = client.get("/api/jobs/status", headers={HEADER: kode}).json()
    api.stop()
    stack.api()
    with stack.client() as client:
        after = client.get("/api/jobs/status", headers={HEADER: kode}).json()
    assert before["status"] == after["status"] == "queued"
    assert before["dibuat"] == after["dibuat"]


def test_two_workers_never_process_same_job(stack, conn, settings):
    from celery import Celery

    from konversiku.settings import broker_url

    stack.api()
    stack.worker("pekerja1@uji", delay=1)
    stack.worker("pekerja2@uji", delay=1)
    duplicate = Celery(broker=broker_url(settings))
    with stack.client() as client:
        codes = [_submit(client) for _ in range(10)]
        for kode in codes:                             # setiap id dikirim sekali lagi
            duplicate.send_task("konversiku.konversi", args=[str(_job(conn, kode).id)], queue="konversiku")
        _wait(lambda: all(_status(client, kode) == "success" for kode in codes), 120, "10 tugas selesai")
    duplicate.close()
    attempts = [_job(conn, kode).attempts for kode in codes]
    assert attempts == [1] * 10
    workers = {_job(conn, kode).worker for kode in codes}
    assert workers <= {"pekerja1@uji", "pekerja2@uji"}


def test_code_not_in_any_log(stack, settings, tmp_path):
    api = stack.api()
    worker = stack.worker("pekerja1@uji", delay=0)
    with stack.client() as client:
        kode = _submit(client)
        _wait(lambda: _status(client, kode) == "success", 60, "tugas selesai")
        assert client.get("/api/jobs/hasil", headers={HEADER: kode}).status_code == 200
        assert client.get("/api/jobs/status", headers={HEADER: "salah"}).status_code == 400
    recovery = subprocess.run([sys.executable, "-m", "konversiku.recovery"], cwd=APP_DIR,
                              env=stack.env(0), capture_output=True, text=True, timeout=60)
    assert recovery.returncode == 0, recovery.stderr[-2000:]
    logs = {"api": api.text(), "pekerja": worker.text(), "pemulihan": recovery.stdout + recovery.stderr}
    assert '"POST /api/jobs?source=csv&target=json HTTP/1.1" 202' in logs["api"]   # access log aktif
    for name, text in logs.items():
        assert kode not in text, name
