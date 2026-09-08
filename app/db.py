"""SQLite-backed job queue.

Deliberately a database rather than an in-memory list: it survives restarts today,
and handles ~5 concurrent users unchanged when this moves to a VPS. Every job carries
an `owner` from day one so adding login later is a login screen, not a migration.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "h3studio.db"

# queued -> running -> done | failed | cancelled
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    owner         TEXT NOT NULL DEFAULT 'me',
    status        TEXT NOT NULL DEFAULT 'queued',
    prompt        TEXT NOT NULL,
    ref_images    TEXT NOT NULL DEFAULT '[]',   -- json list of uploaded filenames
    seconds       INTEGER NOT NULL DEFAULT 10,
    seed          INTEGER,
    mode          TEXT NOT NULL DEFAULT 't2v',  -- t2v | i2v | r2v
    preset        TEXT NOT NULL DEFAULT 'draft',-- draft (cheap iteration) | final
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    output_path   TEXT,
    remote_id     TEXT                          -- ComfyUI prompt_id, for resuming
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    pod_id        TEXT,
    status        TEXT NOT NULL,        -- booting | ready | stopping | stopped | error
    endpoint      TEXT,
    gpu_type      TEXT,
    started_at    REAL NOT NULL,
    ended_at      REAL,
    cost_estimate REAL NOT NULL DEFAULT 0,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")       # concurrent readers while one writes
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    d["ref_images"] = json.loads(d.get("ref_images") or "[]")
    return d


# ---------- jobs ----------

def add_job(prompt: str, *, seconds: int = 10, ref_images: Iterable[str] = (),
            seed: int | None = None, mode: str = "t2v", preset: str = "draft",
            owner: str = "me") -> str:
    job_id = uuid.uuid4().hex[:12]
    with connect() as con:
        con.execute(
            "INSERT INTO jobs (id, owner, prompt, ref_images, seconds, seed, mode, preset,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, owner, prompt, json.dumps(list(ref_images)), seconds, seed, mode,
             preset, time.time()),
        )
    return job_id


def list_jobs(limit: int = 500) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM jobs ORDER BY"
            "  CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,"
            "  created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row(r) for r in rows]


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as con:
        return _row(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def claim_next_queued() -> dict[str, Any] | None:
    """Atomically move one queued job to running. Safe if two workers ever race."""
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            con.execute("COMMIT")
            return None
        con.execute(
            "UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=?",
            (time.time(), row["id"]),
        )
        con.execute("COMMIT")
        return _row(con.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    if "ref_images" in fields and not isinstance(fields["ref_images"], str):
        fields["ref_images"] = json.dumps(list(fields["ref_images"]))
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as con:
        con.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))


def counts() -> dict[str, int]:
    with connect() as con:
        rows = con.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
    out = {"queued": 0, "running": 0, "done": 0, "failed": 0, "cancelled": 0}
    for r in rows:
        out[r["status"]] = r["c"]
    return out


def requeue_stuck_running() -> int:
    """On startup, anything left 'running' from a previous process is orphaned."""
    with connect() as con:
        cur = con.execute("UPDATE jobs SET status='queued', remote_id=NULL WHERE status='running'")
        return cur.rowcount


# ---------- runs (pod sessions, for cost tracking) ----------

def start_run(pod_id: str | None, gpu_type: str, note: str = "") -> str:
    run_id = uuid.uuid4().hex[:12]
    with connect() as con:
        con.execute(
            "INSERT INTO runs (id, pod_id, status, gpu_type, started_at, note) VALUES (?,?,?,?,?,?)",
            (run_id, pod_id, "booting", gpu_type, time.time(), note),
        )
    return run_id


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as con:
        con.execute(f"UPDATE runs SET {sets} WHERE id=?", (*fields.values(), run_id))


def active_run() -> dict[str, Any] | None:
    with connect() as con:
        r = con.execute(
            "SELECT * FROM runs WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return dict(r) if r else None


def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------- kv (policy, misc settings) ----------

def kv_get(k: str, default: str | None = None) -> str | None:
    with connect() as con:
        r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def kv_set(k: str, v: str) -> None:
    with connect() as con:
        con.execute("INSERT INTO kv (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
