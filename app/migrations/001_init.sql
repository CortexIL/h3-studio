CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id           TEXT PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','done','failed','cancelled')),
    prompt       TEXT NOT NULL,
    ref_images   JSONB NOT NULL DEFAULT '[]'::jsonb,
    seconds      INTEGER NOT NULL DEFAULT 10,
    seed         BIGINT,
    mode         TEXT NOT NULL DEFAULT 'i2v' CHECK (mode IN ('t2v','i2v','r2v')),
    preset       TEXT NOT NULL DEFAULT 'final',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    attempts     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    output_key   TEXT,
    output_bytes BIGINT,
    remote_id    TEXT
);

CREATE INDEX idx_jobs_user_created ON jobs (user_id, created_at DESC);
CREATE INDEX idx_jobs_queue        ON jobs (created_at) WHERE status = 'queued';
CREATE INDEX idx_jobs_archive      ON jobs (user_id, finished_at DESC)
                                   WHERE status = 'done';

CREATE TABLE runs (
    id            TEXT PRIMARY KEY,
    pod_id        TEXT,
    status        TEXT NOT NULL,
    endpoint      TEXT,
    gpu_type      TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at      TIMESTAMPTZ,
    cost_estimate NUMERIC(10,4) NOT NULL DEFAULT 0,
    note          TEXT
);

CREATE TABLE kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
