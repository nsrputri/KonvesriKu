-- KonversiKu tahap 2: status tugas. Aman diulang (IF NOT EXISTS).
-- Kode tugas tidak pernah disimpan; hanya SHA-256-nya (code_hash).
CREATE TABLE IF NOT EXISTS jobs (
    id            uuid PRIMARY KEY,
    code_hash     bytea NOT NULL UNIQUE,
    source        text NOT NULL,
    target        text NOT NULL,
    options       jsonb NOT NULL DEFAULT '{}'::jsonb,
    status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'processing', 'success', 'failed', 'expired')),
    attempts      integer NOT NULL DEFAULT 0,
    max_attempts  integer NOT NULL DEFAULT 3,
    worker        text,
    lease_until   timestamptz,
    error         text,
    notes         jsonb NOT NULL DEFAULT '[]'::jsonb,
    input_bytes   bigint NOT NULL,
    output_bytes  bigint,
    created_at    timestamptz NOT NULL,
    enqueued_at   timestamptz,
    started_at    timestamptz,
    finished_at   timestamptz,
    expires_at    timestamptz
);
CREATE INDEX IF NOT EXISTS jobs_status_enqueued ON jobs (status, enqueued_at);
CREATE INDEX IF NOT EXISTS jobs_status_expires ON jobs (status, expires_at);
CREATE INDEX IF NOT EXISTS jobs_queue_order ON jobs (status, created_at, id);

-- Total tugas berhasil dan gagal sepanjang waktu (untuk /metrics dan Grafana). Bertambah di
-- transaksi yang sama dengan transisi status, dan tidak berkurang saat retensi menghapus baris.
CREATE TABLE IF NOT EXISTS job_totals (
    hasil   text PRIMARY KEY CHECK (hasil IN ('success', 'failed')),
    jumlah  bigint NOT NULL DEFAULT 0
);
INSERT INTO job_totals (hasil) VALUES ('success'), ('failed') ON CONFLICT (hasil) DO NOTHING;
