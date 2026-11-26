"""/healthz dan /metrics untuk pemantauan (Prometheus, proyek 3)."""
from __future__ import annotations

import pytest

from tahap2.bantuan_tahap2 import NOW, add_job

pytestmark = pytest.mark.layanan


@pytest.fixture()
def redis_client(settings):
    import redis

    from konversiku.settings import broker_url

    client = redis.Redis.from_url(broker_url(settings), socket_timeout=5)
    try:
        client.ping()
    except redis.RedisError as exc:
        from tahap2.conftest import _unavailable

        _unavailable("Redis", exc)
    yield client
    client.delete("konversiku")
    client.close()


@pytest.fixture()
def ops(conn, storage, redis_client):
    from fastapi.testclient import TestClient

    import app as app_module
    from konversiku import jobs_api, ops_api

    def shared_conn():
        yield conn

    overrides = app_module.app.dependency_overrides
    overrides[jobs_api.get_conn] = shared_conn
    overrides[jobs_api.get_storage] = lambda: storage
    overrides[ops_api.get_db_check] = lambda: (lambda: conn.execute("SELECT 1").fetchone())
    overrides[ops_api.get_redis] = lambda: redis_client
    with TestClient(app_module.app) as client:
        yield client
    overrides.clear()


def test_healthz_ok(ops):
    response = ops.get("/healthz")
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok",
                               "komponen": {"postgresql": "ok", "redis": "ok", "penyimpanan": "ok"}}
    assert response.headers["cache-control"] == "no-store"


def test_healthz_names_failed_component(ops):
    import redis

    import app as app_module
    from konversiku import ops_api

    class DeadRedis:
        def ping(self):
            raise redis.ConnectionError("mati")

    app_module.app.dependency_overrides[ops_api.get_redis] = lambda: DeadRedis()
    response = ops.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "gagal",
                               "komponen": {"postgresql": "ok", "redis": "gagal", "penyimpanan": "ok"}}


def test_healthz_reports_database_down(ops):
    import psycopg

    import app as app_module
    from konversiku import ops_api

    def down():
        raise psycopg.OperationalError("mati")

    app_module.app.dependency_overrides[ops_api.get_db_check] = lambda: down
    body = ops.get("/healthz").json()
    assert body["status"] == "gagal" and body["komponen"]["postgresql"] == "gagal"


def _families(text: str) -> dict:
    from prometheus_client.parser import text_string_to_metric_families

    return {family.name: family for family in text_string_to_metric_families(text)}


def test_metrics_counts_match_database(ops, conn, redis_client):
    from konversiku import jobs

    add_job(conn)
    add_job(conn)
    claimed = add_job(conn)
    jobs.claim(conn, claimed, "pekerja1@uji", NOW)
    done = add_job(conn)
    jobs.claim(conn, done, "pekerja1@uji", NOW)
    jobs.mark_success(conn, done, 5, [], NOW)
    conn.commit()
    redis_client.rpush("konversiku", "a", "b", "c")

    response = ops.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    families = _families(response.text)
    by_status = {s.labels["status"]: s.value for s in families["konversiku_tugas"].samples}
    expected = jobs.count_by_status(conn)
    conn.commit()
    assert by_status == {status: float(n) for status, n in expected.items()}
    assert set(by_status) == {"queued", "processing", "success", "failed", "expired"}
    assert families["konversiku_antrean_redis"].samples[0].value == 3


def test_metrics_totals_survive_expiry_and_row_deletion(ops, conn, storage):
    """Total berhasil/gagal untuk Grafana tidak hilang saat tugas kedaluwarsa dan barisnya dihapus."""
    from datetime import timedelta

    from konversiku import cleanup, jobs

    ok = add_job(conn)
    jobs.claim(conn, ok, "pekerja1@uji", NOW)
    assert jobs.mark_success(conn, ok, 5, [], NOW) is True
    bad = add_job(conn)
    jobs.claim(conn, bad, "pekerja1@uji", NOW)
    assert jobs.mark_failed(conn, bad, "File rusak.", NOW) is True
    conn.commit()
    cleanup.cleanup(conn, storage, NOW + timedelta(hours=25))
    cleanup.cleanup(conn, storage, NOW + timedelta(days=9))
    assert sum(jobs.count_by_status(conn).values()) == 0
    conn.commit()
    families = _families(ops.get("/metrics").text)
    totals = {s.labels["hasil"]: s.value for s in families["konversiku_tugas_selesai"].samples
              if s.name.endswith("_total")}
    assert totals == {"success": 1.0, "failed": 1.0}


def test_metrics_contain_no_ids_or_codes(ops, conn):
    from konversiku import jobs

    kode, hashed = jobs.new_code()
    job_id = add_job(conn)
    conn.execute("UPDATE jobs SET code_hash = %s WHERE id = %s", (hashed, job_id))
    conn.commit()
    text = ops.get("/metrics").text
    assert kode not in text
    assert str(job_id) not in text
    assert hashed.hex() not in text
