"""API tugas: submit, status, unduh, kode di header, kegagalan antara file dan database."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from conftest import RECORDS, data_sample, records_of

from tahap2.bantuan_tahap2 import add_job

pytestmark = pytest.mark.layanan

HEADER = "X-Kode-Tugas"
SERVICE_DOWN = "Layanan tugas sedang tidak tersedia. Coba lagi nanti."


@pytest.fixture()
def api(conn, storage):
    from fastapi.testclient import TestClient

    import app as app_module
    from konversiku import jobs_api

    sent: list = []

    def shared_conn():
        yield conn

    app_module.app.dependency_overrides[jobs_api.get_conn] = shared_conn
    app_module.app.dependency_overrides[jobs_api.get_storage] = lambda: storage
    app_module.app.dependency_overrides[jobs_api.get_sender] = lambda: sent.append
    with TestClient(app_module.app) as client:
        client.sent = sent
        yield client
    app_module.app.dependency_overrides.clear()


def _submit(api, source="csv", target="json", data=None, **params):
    body = data_sample(source) if data is None else data
    return api.post("/api/jobs", params={"source": source, "target": target, **params}, content=body,
                    headers={"Content-Type": "application/octet-stream"})


def _job_of(conn, kode):
    from konversiku import jobs

    return jobs.find_by_code_hash(conn, jobs.hash_code(kode))


def _nothing_stored(conn, storage):
    rows = conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"]
    conn.commit()
    return rows == 0 and list(storage.incoming.iterdir()) == [] and storage.job_ids_on_disk() == []


def _process(conn, storage, kode):
    from konversiku import worker

    assert worker.process_job(conn, storage, _job_of(conn, kode).id, "pekerja1@uji") == "success"


def test_submit_returns_202_and_queues(api, conn, storage):
    data = data_sample("csv")
    response = _submit(api, data=data)
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "queued" and len(body["kode"]) == 43
    job = _job_of(conn, body["kode"])
    assert (job.status, job.source, job.target, job.input_bytes) == ("queued", "csv", "json", len(data))
    assert job.options == {"formula_guard": True} or job.options == {}
    assert api.sent == [job.id]
    assert storage.read_input(job.id) == data


def test_status_and_download_roundtrip(api, conn, storage):
    kode = _submit(api).json()["kode"]
    _process(conn, storage, kode)
    status = api.get("/api/jobs/status", headers={HEADER: kode})
    assert status.status_code == 200, status.text
    info = status.json()
    assert (info["status"], info["source"], info["target"]) == ("success", "csv", "json")
    assert info["posisi_antrean"] is None and info["pesan"] is None
    assert info["selesai"] and info["kedaluwarsa"]
    assert isinstance(info["catatan"], list)
    result = api.get("/api/jobs/hasil", headers={HEADER: kode})
    assert result.status_code == 200
    assert result.headers["content-disposition"] == 'attachment; filename="hasil.json"'
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in result.headers["content-security-policy"]
    assert isinstance(json.loads(result.headers["x-konversiku-notes"]), list)
    assert records_of("json", result.content) == RECORDS


def test_status_while_queued_shows_position(api):
    kode = _submit(api).json()["kode"]
    info = api.get("/api/jobs/status", headers={HEADER: kode}).json()
    assert (info["status"], info["posisi_antrean"], info["selesai"]) == ("queued", 1, None)


def test_download_before_done_is_409(api):
    kode = _submit(api).json()["kode"]
    response = api.get("/api/jobs/hasil", headers={HEADER: kode})
    assert response.status_code == 409
    assert response.json()["detail"] == "Tugas belum selesai."


def test_failed_job_status_and_download(api, conn, storage):
    from konversiku import worker

    kode = _submit(api, source="json", target="csv", data=b'{"rusak": ').json()["kode"]
    assert worker.process_job(conn, storage, _job_of(conn, kode).id, "pekerja1@uji") == "failed"
    info = api.get("/api/jobs/status", headers={HEADER: kode}).json()
    assert info["status"] == "failed" and info["pesan"]
    response = api.get("/api/jobs/hasil", headers={HEADER: kode})
    assert response.status_code == 409
    assert response.json()["detail"] == info["pesan"]


def test_unknown_code_is_404(api):
    from konversiku import jobs

    kode, _ = jobs.new_code()
    for path in ("/api/jobs/status", "/api/jobs/hasil"):
        response = api.get(path, headers={HEADER: kode})
        assert response.status_code == 404
        assert response.json()["detail"] == "Kode tugas tidak ditemukan."


def test_expired_is_410(api, conn, storage):
    from konversiku import cleanup, jobs

    kode = _submit(api).json()["kode"]
    _process(conn, storage, kode)
    cleanup.cleanup(conn, storage, jobs.utcnow() + timedelta(hours=25))
    info = api.get("/api/jobs/status", headers={HEADER: kode}).json()
    assert info["status"] == "expired"
    assert info["pesan"] == "Hasil sudah kedaluwarsa dan dihapus."
    response = api.get("/api/jobs/hasil", headers={HEADER: kode})
    assert response.status_code == 410
    assert response.json()["detail"] == "Hasil sudah kedaluwarsa dan dihapus."


def test_success_past_expiry_before_cleanup_is_410(api, conn, storage):
    kode = _submit(api).json()["kode"]
    _process(conn, storage, kode)
    conn.execute("UPDATE jobs SET expires_at = now() - interval '1 minute'")
    conn.commit()
    assert api.get("/api/jobs/hasil", headers={HEADER: kode}).status_code == 410


def test_code_header_validation(api):
    kode = _submit(api).json()["kode"]
    assert api.get("/api/jobs/status").status_code == 400
    for bad in ("../etc", "a" * 200, "", kode[:-1] + "!"):
        response = api.get("/api/jobs/status", headers={HEADER: bad})
        assert response.status_code == 400, bad
        assert response.json()["detail"] == "Kode tugas tidak valid."
    assert api.get("/api/jobs/status", headers={HEADER: f"  {kode}\t"}).status_code == 200


def test_empty_upload_is_400(api, conn, storage):
    response = _submit(api, data=b"")
    assert response.status_code == 400
    assert _nothing_stored(conn, storage)


def test_upload_over_limit_413_leaves_nothing(api, conn, storage, monkeypatch):
    from konversiku import limits

    monkeypatch.setitem(limits.MAX_UPLOAD_BYTES_BY_SOURCE, "csv", 10)
    response = _submit(api, data=b"a,b\n" * 10)
    assert response.status_code == 413
    assert _nothing_stored(conn, storage)
    assert api.sent == []


def test_submit_respects_per_source_limit(api, monkeypatch):
    """File tepat di batas format diterima; 1 byte di atasnya ditolak dengan nama formatnya."""
    from konversiku import limits

    monkeypatch.setitem(limits.MAX_UPLOAD_BYTES_BY_SOURCE, "csv", 10)
    assert _submit(api, data=b"a,b\n1,2\n12").status_code == 202
    response = _submit(api, data=b"a,b\n1,2\n123")
    assert response.status_code == 413
    assert "CSV" in response.json()["detail"]


def test_over_limit_message_shows_mib_and_mb(api, monkeypatch):
    from konversiku import limits

    monkeypatch.setitem(limits.MAX_UPLOAD_BYTES_BY_SOURCE, "csv", 2 * limits.MiB)
    response = _submit(api, data=b"a,b\n" + b"1,2\n" * (limits.MiB // 2))
    assert response.status_code == 413
    assert response.json()["detail"] == "File CSV melebihi batas 2 MiB (2,1 MB)."


def test_aborted_upload_leaves_no_files_or_rows(api, conn, storage):
    """Klien memutus koneksi di tengah unggahan: tidak ada .part tersisa dan tidak ada baris."""
    import app as app_module

    messages = [
        {"type": "http.request", "body": b"a,b\n1,2\n", "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent_back = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent_back.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/api/jobs", "raw_path": b"/api/jobs",
             "query_string": b"source=csv&target=json", "root_path": "",
             "headers": [(b"host", b"uji"), (b"content-type", b"application/octet-stream")],
             "client": ("127.0.0.1", 1), "server": ("uji", 80)}
    asyncio.run(app_module.app(scope, receive, send))
    assert _nothing_stored(conn, storage)
    assert api.sent == []


def test_queue_full_is_503(api, conn, monkeypatch):
    from konversiku import limits

    monkeypatch.setattr(limits, "MAX_QUEUED_JOBS", 1)
    add_job(conn)
    response = _submit(api)
    assert response.status_code == 503
    assert response.json()["detail"] == "Antrean sedang penuh. Coba lagi beberapa menit lagi."


def test_quota_full_is_503(api, storage, monkeypatch):
    from konversiku import limits

    monkeypatch.setattr(limits, "DATA_QUOTA_BYTES", 5)
    (storage.incoming / "lama.part").write_bytes(b"123456")
    response = _submit(api)
    assert response.status_code == 503
    assert response.json()["detail"] == "Penyimpanan server sedang penuh. Coba lagi nanti."


def test_send_failure_still_202_and_stays_queued(api, conn):
    import app as app_module
    from konversiku import jobs_api

    def broken(_job_id):
        raise ConnectionError("Redis mati")

    app_module.app.dependency_overrides[jobs_api.get_sender] = lambda: broken
    response = _submit(api)
    assert response.status_code == 202
    job = _job_of(conn, response.json()["kode"])
    assert (job.status, job.enqueued_at) == ("queued", None)


def test_db_failure_before_commit_removes_file(api, conn, storage, monkeypatch):
    import psycopg

    from konversiku import jobs

    def broken_insert(*args, **kwargs):
        raise psycopg.OperationalError("koneksi putus")

    monkeypatch.setattr(jobs, "insert_job", broken_insert)
    response = _submit(api)
    assert response.status_code == 503
    assert response.json()["detail"] == SERVICE_DOWN
    assert _nothing_stored(conn, storage)
    assert api.sent == []


def test_database_down_returns_503(api):
    import psycopg

    import app as app_module
    from konversiku import jobs, jobs_api

    def down():
        raise psycopg.OperationalError("PostgreSQL mati")

    app_module.app.dependency_overrides[jobs_api.get_conn] = down
    kode, _ = jobs.new_code()
    for response in (_submit(api), api.get("/api/jobs/status", headers={HEADER: kode})):
        assert response.status_code == 503
        assert response.json()["detail"] == SERVICE_DOWN


def test_unsupported_pair_400_and_missing_tool_503(api, conn, storage, monkeypatch):
    from konversiku import registry

    response = _submit(api, source="csv", target="pdf")
    assert response.status_code == 400
    monkeypatch.setattr(registry, "unavailable_reason", lambda pair, found: "Alat uji belum terpasang.")
    response = _submit(api)
    assert response.status_code == 503
    assert response.json()["detail"] == "Alat uji belum terpasang."
    assert _nothing_stored(conn, storage)
