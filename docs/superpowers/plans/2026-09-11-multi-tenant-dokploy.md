# Multi-Tenant H3 Studio on Dokploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-user desktop H3 Studio into a hosted multi-user service on Dokploy, where admin-created users each queue clips against one shared rented GPU and see only their own jobs and archive.

**Architecture:** One FastAPI container backed by Postgres and a private S3-compatible bucket. Signed-cookie sessions gate every API route; every job row carries a `user_id` and every read is filtered by it. A single in-process orchestrator, protected by a Postgres advisory lock, owns the one rented RunPod pod and drains a global FIFO queue. Finished MP4s go to S3 and are streamed back through an ownership check.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, psycopg 3 (async pool), Postgres 16, argon2-cffi, itsdangerous, boto3, pydantic-settings, pytest + pytest-asyncio, Docker.

**Spec:** `docs/superpowers/specs/2026-09-11-multi-tenant-dokploy-design.md`

## Global Constraints

These apply to every task. Do not restate them per task; do not violate them.

- **Python 3.12.** Use `from __future__ import annotations` and PEP 604 unions (`str | None`), matching the existing codebase.
- **No ORM.** Raw SQL through psycopg 3, matching the existing `db.py` style.
- **Every `/api/*` route requires authentication** except `POST /api/auth/login` and `GET /api/health`. Authentication is applied by an explicit FastAPI dependency on each router — never by path-matching middleware.
- **Another user's resource returns 404, never 403.** A 403 confirms the resource exists.
- **Nothing writes to disk at runtime.** No `config.yaml` saves, no local output folder in production. The container filesystem is ephemeral.
- **`uvicorn --workers 1`.** The orchestrator is an in-process singleton that owns a rented GPU. Two of them means two pods billing at once, silently.
- **Never log a password, a password hash, a session cookie, the RunPod API key, or S3 credentials.** The RunPod key may be referred to by its last four characters only.
- **Comments explain why, not what.** The existing codebase's comments justify decisions; match that. Do not add comments that restate the code.
- **Every task ends with a commit.** Commit messages: imperative subject under 72 characters, body explaining why when it is not obvious.
- **TDD.** Write the failing test, watch it fail, write the minimum code, watch it pass, commit.

## Pre-flight: local Postgres for tests

The test suite needs a real Postgres. Before Task 1, run:

```bash
docker run -d --name h3-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=h3_test -p 5433:5432 postgres:16-alpine
```

Tests read `TEST_DATABASE_URL`, defaulting to `postgresql://postgres:postgres@localhost:5433/h3_test`.

## File Structure

`app/db.py` (202 lines, SQLite, one connection per call) and `app/main.py` (627 lines, every route plus the CLI) are both replaced by focused modules. Everything else keeps its current home.

**New:**

| File | Responsibility |
|---|---|
| `app/settings.py` | Environment-derived settings, fail-fast on missing required values |
| `app/store/__init__.py` | Re-exports the store API |
| `app/store/pool.py` | Async connection pool lifecycle, migration runner, advisory lock |
| `app/store/users.py` | User rows: create, authenticate, disable, list |
| `app/store/jobs.py` | Job rows, all user-scoped; the claim query |
| `app/store/runs.py` | Pod session rows for cost tracking |
| `app/store/kv.py` | Admin-settable runtime settings |
| `app/migrations/001_init.sql` | The whole schema |
| `app/auth.py` | Password hashing, session cookies, `current_user` / `require_admin` dependencies |
| `app/storage.py` | S3 client: put, get, stream ranges, delete |
| `app/sinks/s3.py` | `S3Sink` implementing `OutputSink` |
| `app/routes/auth.py` | Login, logout, `/api/me` |
| `app/routes/jobs.py` | Queue and job CRUD, own-only |
| `app/routes/archive.py` | Paginated finished clips, own-only |
| `app/routes/media.py` | Video and reference-image streaming, own-only |
| `app/routes/admin.py` | Users, all jobs, policy, RunPod key, budget, runs |
| `app/routes/status.py` | The polling endpoint and `/api/health` |
| `app/batch.py` | The zip/txt/json batch parser, extracted from `inbox.py` |
| `web/login.html`, `web/archive.html`, `web/admin.html` | New pages |
| `web/auth.js`, `web/archive.js`, `web/admin.js` | Their scripts |
| `Dockerfile`, `.dockerignore`, `docker-compose.yml` | Container |
| `docs/DEPLOY.md` | Dokploy runbook |
| `CLAUDE.md` | Repository guide for future agents |

**Modified:** `app/config.py`, `app/main.py`, `app/orchestrator.py`, `app/sinks/__init__.py`, `app/backends/__init__.py`, `app/backends/comfy.py`, `app/backends/runpod_pod.py`, `app/backends/mock.py`, `web/index.html`, `web/app.js`, `web/style.css`, `requirements.txt`.

**Deleted:** `app/launch.py`, `app/db.py`, `app/inbox.py`, `app/mcp_server.py`, `app/install_mcp.py`, `H3 Studio.bat`, `H3 Studio (Demo).bat`, `h3studio.ico`.

---
### Task 1: Dependencies, settings, and the test harness

**Files:**
- Create: `app/settings.py`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Create: `pytest.ini`
- Modify: `requirements.txt`
- Create: `requirements-dev.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: `app.settings.Settings` (pydantic-settings model) and `app.settings.get_settings() -> Settings` (cached). Fields used by later tasks: `database_url: str`, `session_secret: str`, `s3_endpoint: str`, `s3_bucket: str`, `s3_access_key: str`, `s3_secret_key: str`, `s3_region: str`, `admin_email: str | None`, `admin_password: str | None`, `runpod_api_key: str`, `pod_policy: str`, `budget_session_limit_usd: float`, `mock: bool`, `output_sink: str`, `local_output_folder: str`, `keep_audio: bool`, `session_max_age_days: int`, `cookie_secure: bool`, `host: str`, `port: int`.

- [ ] **Step 1: Add the runtime dependencies**

Replace `requirements.txt` with:

```
# Web app
fastapi>=0.141.1
uvicorn[standard]>=0.34.0
httpx>=0.28.1
pydantic>=2.10.4
pydantic-settings>=2.7.0
python-multipart>=0.0.20

# Persistence
psycopg[binary,pool]>=3.2.3

# Auth
argon2-cffi>=23.1.0
itsdangerous>=2.2.0

# Object storage
boto3>=1.35.0
```

`pyyaml` is dropped: configuration comes from the environment now, and nothing writes a config file.

- [ ] **Step 2: Add the dev dependencies**

Create `requirements-dev.txt`:

```
-r requirements.txt
pytest>=8.3.0
pytest-asyncio>=0.25.0
moto[s3]>=5.0.0
```

- [ ] **Step 3: Configure pytest**

Create `pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
filterwarnings = error::DeprecationWarning
```

`asyncio_mode = auto` means `async def test_…` needs no decorator, which keeps the isolation tests readable.

- [ ] **Step 4: Write the failing test for settings**

Create `tests/__init__.py` (empty) and `tests/test_settings.py`:

```python
from __future__ import annotations

import pytest

from app.settings import Settings


def _minimal(**over):
    base = dict(
        DATABASE_URL="postgresql://u:p@h/db",
        SESSION_SECRET="x" * 32,
        S3_ENDPOINT="http://minio:9000",
        S3_BUCKET="h3",
        S3_ACCESS_KEY="ak",
        S3_SECRET_KEY="sk",
    )
    base.update(over)
    return base


def test_loads_from_environment(monkeypatch):
    for k, v in _minimal().items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.database_url == "postgresql://u:p@h/db"
    assert s.s3_bucket == "h3"
    assert s.pod_policy == "off"          # money-safe default
    assert s.mock is False


def test_missing_required_names_the_variable(monkeypatch):
    env = _minimal()
    env.pop("SESSION_SECRET")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError) as exc:
        Settings()
    assert "SESSION_SECRET" in str(exc.value)


def test_short_session_secret_is_rejected(monkeypatch):
    for k, v in _minimal(SESSION_SECRET="tooshort").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError):
        Settings()


def test_pod_policy_must_be_known(monkeypatch):
    for k, v in _minimal(POD_POLICY="sometimes").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError):
        Settings()
```

- [ ] **Step 5: Run it and watch it fail**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.settings'`

- [ ] **Step 6: Write `app/settings.py`**

```python
"""Every knob this process reads, and where it comes from.

Configuration is environment-only. The desktop build wrote config.yaml back to
disk from the UI; a container filesystem does not survive a redeploy, so the
settings an admin can change at runtime - RunPod key, pod policy, budget - live in
the `kv` table instead, and the values here are only their initial defaults.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

POLICIES = {"auto", "keep-warm", "off"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, case_sensitive=False,
                                      extra="ignore")

    # --- required: the app refuses to start without these ---
    database_url: str
    # Never generated at boot: a fresh secret would sign every user out on each
    # redeploy, and two replicas would not agree on it.
    session_secret: str
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str

    # --- optional ---
    s3_region: str = "us-east-1"
    admin_email: str | None = None
    admin_password: str | None = None
    runpod_api_key: str = ""
    # 'off' rather than 'auto': auto rents a GPU the moment anyone queues, and a
    # fresh deploy should never start billing before an admin says so.
    pod_policy: Literal["auto", "keep-warm", "off"] = "off"
    budget_session_limit_usd: float = 8.0
    mock: bool = False
    output_sink: Literal["s3", "local"] = "s3"
    local_output_folder: str = "/data/out"
    # H3 emits audio with every clip. It is real at thirty steps and unusable
    # noise under the four-step turbo LoRA, which distils the video branch only -
    # so this is a per-install choice, not a default worth flipping for everyone.
    keep_audio: bool = True
    session_max_age_days: int = 14
    # Only ever false for local HTTP development; a Secure cookie is not sent
    # over plain HTTP, so login would silently never stick.
    cookie_secure: bool = True
    host: str = "0.0.0.0"
    port: int = 8777

    @field_validator("session_secret")
    @classmethod
    def _secret_long_enough(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters")
        return v

    @field_validator("database_url")
    @classmethod
    def _pg_only(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must be a postgresql:// URL")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

Pydantic's `ValidationError` subclasses `ValueError` and names each missing field, which satisfies both failure tests.

- [ ] **Step 7: Run the settings tests**

Run: `python -m pytest tests/test_settings.py -v`
Expected: PASS (4 tests)

- [ ] **Step 8: Write the shared test fixtures**

Create `tests/conftest.py`:

```python
"""Fixtures shared by the whole suite.

The database is real Postgres rather than a stub: the code under test relies on
FOR UPDATE SKIP LOCKED, advisory locks and JSONB, none of which a fake reproduces,
and getting those wrong is exactly the class of bug worth catching.
"""
from __future__ import annotations

import os

import psycopg
import pytest
import pytest_asyncio

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/h3_test",
)


def _reachable() -> bool:
    try:
        with psycopg.connect(TEST_DSN, connect_timeout=2):
            return True
    except psycopg.Error:
        return False


def pytest_collection_modifyitems(config, items):
    if _reachable():
        return
    skip = pytest.mark.skip(
        reason=f"No Postgres at {TEST_DSN}. Start one:\n"
               "  docker run -d --name h3-test-pg -e POSTGRES_PASSWORD=postgres "
               "-e POSTGRES_DB=h3_test -p 5433:5432 postgres:16-alpine"
    )
    for item in items:
        if "db" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def dsn() -> str:
    return TEST_DSN
```

The suite skips loudly with the exact command to fix it rather than erroring
sixty times, and only the tests that actually need a database are skipped.

- [ ] **Step 9: Verify collection works with and without Postgres**

Run: `python -m pytest -v`
Expected: PASS — the settings tests run; nothing errors during collection.

- [ ] **Step 10: Commit**

```bash
git add requirements.txt requirements-dev.txt pytest.ini app/settings.py tests/
git commit -m "Read configuration from the environment, not a config file

A container filesystem does not survive a redeploy, so the desktop habit of
saving config.yaml from the UI cannot work here. Settings an admin changes at
runtime will live in the database; these are their initial values.

POD_POLICY defaults to off rather than auto: auto rents a GPU the moment
anyone queues, and a fresh deploy should not start billing unasked."
```

---

### Task 2: Postgres pool, schema, and the migration runner

**Files:**
- Create: `app/store/__init__.py`, `app/store/pool.py`
- Create: `app/migrations/001_init.sql`
- Create: `tests/test_migrations.py`
- Delete: nothing yet (`app/db.py` goes in Task 6)

**Interfaces:**
- Consumes: `app.settings.get_settings`.
- Produces:
  - `async open_pool(dsn: str) -> AsyncConnectionPool` — creates and opens the module-level pool, retrying for 60s.
  - `async close_pool() -> None`
  - `pool() -> AsyncConnectionPool` — the open pool; raises `RuntimeError` if not open.
  - `async migrate(conn) -> list[str]` — applies pending files, returns filenames applied.
  - `async try_advisory_lock(conn, key: int) -> bool`
  - `ORCHESTRATOR_LOCK_KEY: int` constant.
  - Context manager `async with connection() as conn:` yielding a pooled `AsyncConnection` with `row_factory=dict_row`.

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_migrations.py`:

```python
from __future__ import annotations

import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import dict_row

from app.store import pool as pool_mod


@pytest_asyncio.fixture
async def db(dsn):
    """A clean schema per test, with migrations applied."""
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await pool_mod.open_pool(dsn)
    async with pool_mod.connection() as conn:
        await pool_mod.migrate(conn)
    yield pool_mod
    await pool_mod.close_pool()


async def test_migrate_creates_every_table(db):
    async with db.connection() as conn:
        rows = await (await conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public'"
        )).fetchall()
    names = {r["table_name"] for r in rows}
    assert {"users", "jobs", "runs", "kv", "schema_migrations"} <= names


async def test_migrate_is_idempotent(db):
    async with db.connection() as conn:
        applied = await db.migrate(conn)
    assert applied == []          # nothing left to do the second time


async def test_advisory_lock_is_exclusive(db, dsn):
    async with db.connection() as a:
        assert await db.try_advisory_lock(a, db.ORCHESTRATOR_LOCK_KEY) is True
        async with await psycopg.AsyncConnection.connect(dsn) as b:
            b.row_factory = dict_row
            assert await db.try_advisory_lock(b, db.ORCHESTRATOR_LOCK_KEY) is False


async def test_jobs_requires_a_real_user(db):
    async with db.connection() as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await conn.execute(
                "INSERT INTO jobs (id, user_id, prompt) "
                "VALUES ('j1', '00000000-0000-0000-0000-000000000001', 'hi')"
            )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_migrations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.store'`

- [ ] **Step 3: Write the schema**

Create `app/migrations/001_init.sql`:

```sql
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
```

`seed` is `BIGINT`: ComfyUI seeds routinely exceed 32 bits, and the SQLite
column was untyped enough to hide it.

- [ ] **Step 4: Write `app/store/pool.py`**

```python
"""Connection pool, schema migrations, and the orchestrator's advisory lock.

The SQLite version opened a fresh connection on every call, synchronously, from
inside async request handlers. Against a local file that was merely wasteful;
against Postgres over a socket it blocks the event loop for every connected user
at once, and the two-second status poll multiplies that by the number of open
tabs. Hence a pool, and async all the way down.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger("h3studio.store")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

# Arbitrary but fixed: any process holding this owns the rented GPU.
ORCHESTRATOR_LOCK_KEY = 0x4833_5354

_pool: AsyncConnectionPool | None = None


async def open_pool(dsn: str, *, timeout: float = 60.0) -> AsyncConnectionPool:
    """Open the pool, waiting for Postgres to accept connections.

    Dokploy may start this container before the database is ready. Retrying for a
    minute turns a routine startup race into a non-event; failing after it lets
    the platform restart us rather than serving 500s forever.
    """
    global _pool
    if _pool is not None:
        return _pool
    p = AsyncConnectionPool(dsn, min_size=1, max_size=10, open=False,
                            kwargs={"row_factory": dict_row})
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            await p.open(wait=True, timeout=5)
            break
        except Exception as e:
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"database unreachable after {timeout:.0f}s: {e}")
            log.warning("waiting for Postgres: %s", e)
            await asyncio.sleep(2)
    _pool = p
    return p


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("connection pool is not open")
    return _pool


@asynccontextmanager
async def connection():
    async with pool().connection() as conn:
        yield conn


async def migrate(conn) -> list[str]:
    """Apply pending .sql files in filename order, inside one transaction.

    Plain SQL files rather than a migration framework: the schema is four tables
    in an ORM-less codebase, and a thirty-line runner is easier to reason about at
    deploy time than a tool whose autogenerate would have nothing to introspect.
    """
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " filename TEXT PRIMARY KEY,"
        " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    done = {r["filename"] for r in
            await (await conn.execute("SELECT filename FROM schema_migrations")).fetchall()}
    applied: list[str] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in done:
            continue
        log.info("applying migration %s", path.name)
        await conn.execute(path.read_text(encoding="utf-8"))
        await conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)",
                           (path.name,))
        applied.append(path.name)
    await conn.commit()
    return applied


async def try_advisory_lock(conn, key: int) -> bool:
    """Session-level lock, held until this connection closes.

    Session-level rather than transaction-level on purpose: the orchestrator holds
    it for the whole life of the process, not for one statement.
    """
    row = await (await conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (key,))).fetchone()
    return bool(row["got"])
```

Create `app/store/__init__.py`:

```python
"""Persistence. One module per table, plus the pool that serves them all."""
from __future__ import annotations

from .pool import (ORCHESTRATOR_LOCK_KEY, close_pool, connection, migrate,
                   open_pool, pool, try_advisory_lock)

__all__ = ["ORCHESTRATOR_LOCK_KEY", "close_pool", "connection", "migrate",
           "open_pool", "pool", "try_advisory_lock"]
```

- [ ] **Step 5: Run the migration tests**

Run: `python -m pytest tests/test_migrations.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add app/store app/migrations tests/test_migrations.py
git commit -m "Replace the SQLite file with a pooled async Postgres client

db.py opened a connection per call, synchronously, from async handlers. On a
local file that was wasteful; over a socket it stalls the event loop for every
connected user at once.

Adds the advisory lock the orchestrator will take. Two orchestrators would mean
two rented pods billing simultaneously, and nothing would error - the bill would
just double."
```

---
### Task 3: Users and password hashing

**Files:**
- Create: `app/store/users.py`
- Create: `tests/test_users.py`
- Modify: `app/store/__init__.py`
- Modify: `tests/conftest.py` (add the reusable `db` fixture)

**Interfaces:**
- Consumes: `app.store.pool.connection`.
- Produces:
  - `hash_password(raw: str) -> str`
  - `verify_password(hash_: str, raw: str) -> bool`
  - `async create(email: str, password: str, *, role: str = "user") -> dict` — raises `EmailTaken`
  - `async by_id(user_id: str) -> dict | None`
  - `async by_email(email: str) -> dict | None`
  - `async authenticate(email: str, password: str) -> dict | None`
  - `async set_active(user_id: str, active: bool) -> None` — bumps `token_version` when disabling
  - `async set_password(user_id: str, password: str) -> None` — bumps `token_version`
  - `async list_all() -> list[dict]`
  - `async count() -> int`
  - `async ensure_bootstrap_admin(email: str | None, password: str | None) -> dict | None`
  - Exception `EmailTaken`
- A user dict never contains `password_hash` when it leaves this module via `by_id` / `list_all`; `authenticate` reads it internally.

- [ ] **Step 1: Move the `db` fixture into conftest so every task can use it**

Append to `tests/conftest.py`:

```python
import psycopg
import pytest_asyncio
from psycopg.rows import dict_row

from app.store import pool as pool_mod


@pytest_asyncio.fixture
async def db(dsn):
    """A clean, migrated schema per test."""
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await pool_mod.open_pool(dsn)
    async with pool_mod.connection() as conn:
        await pool_mod.migrate(conn)
    yield pool_mod
    await pool_mod.close_pool()
```

Then delete the duplicate fixture from `tests/test_migrations.py`, keeping its imports of `psycopg`, `pytest` and `dict_row`.

- [ ] **Step 2: Write the failing user tests**

Create `tests/test_users.py`:

```python
from __future__ import annotations

import pytest

from app.store import users


async def test_create_and_fetch(db):
    u = await users.create("Ann@Example.COM", "correct horse battery")
    assert u["email"] == "ann@example.com"       # normalised
    assert u["role"] == "user"
    assert "password_hash" not in u
    assert (await users.by_email("ANN@example.com"))["id"] == u["id"]


async def test_duplicate_email_is_refused(db):
    await users.create("a@b.c", "pw-one-long-enough")
    with pytest.raises(users.EmailTaken):
        await users.create("A@B.C", "pw-two-long-enough")


async def test_authenticate(db):
    await users.create("a@b.c", "s3cret-passphrase")
    assert await users.authenticate("a@b.c", "s3cret-passphrase") is not None
    assert await users.authenticate("a@b.c", "wrong") is None
    assert await users.authenticate("nobody@b.c", "s3cret-passphrase") is None


async def test_disabled_user_cannot_authenticate(db):
    u = await users.create("a@b.c", "s3cret-passphrase")
    await users.set_active(u["id"], False)
    assert await users.authenticate("a@b.c", "s3cret-passphrase") is None


async def test_disabling_bumps_token_version(db):
    u = await users.create("a@b.c", "s3cret-passphrase")
    before = (await users.by_id(u["id"]))["token_version"]
    await users.set_active(u["id"], False)
    assert (await users.by_id(u["id"]))["token_version"] == before + 1


async def test_password_change_bumps_token_version(db):
    u = await users.create("a@b.c", "s3cret-passphrase")
    before = (await users.by_id(u["id"]))["token_version"]
    await users.set_password(u["id"], "a-brand-new-passphrase")
    assert (await users.by_id(u["id"]))["token_version"] == before + 1
    assert await users.authenticate("a@b.c", "a-brand-new-passphrase") is not None


async def test_bootstrap_creates_admin_only_when_empty(db):
    a = await users.ensure_bootstrap_admin("boss@h3.local", "bootstrap-passphrase")
    assert a is not None and a["role"] == "admin"
    again = await users.ensure_bootstrap_admin("other@h3.local", "another-passphrase")
    assert again is None
    assert await users.count() == 1


async def test_bootstrap_without_credentials_does_nothing(db):
    assert await users.ensure_bootstrap_admin(None, None) is None
    assert await users.count() == 0


def test_hash_round_trip():
    h = users.hash_password("hunter2-but-longer")
    assert h != "hunter2-but-longer"
    assert users.verify_password(h, "hunter2-but-longer") is True
    assert users.verify_password(h, "hunter3-but-longer") is False


def test_verify_survives_a_corrupt_hash():
    assert users.verify_password("not-a-hash", "anything") is False
```

- [ ] **Step 3: Run and watch it fail**

Run: `python -m pytest tests/test_users.py -v`
Expected: FAIL — `ImportError: cannot import name 'users' from 'app.store'`

- [ ] **Step 4: Write `app/store/users.py`**

```python
"""User rows and password handling.

Passwords are argon2 at library defaults. `token_version` is what makes stateless
session cookies revocable: bumping it invalidates every cookie already issued to
that user without keeping a session table.
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerifyMismatchError

from .pool import connection

log = logging.getLogger("h3studio.users")

_ph = PasswordHasher()

# Verified against when the email does not exist, so a missing account costs the
# same work as a wrong password and response time does not enumerate users.
_DUMMY_HASH = _ph.hash("a-password-that-is-never-anyone-s")

PUBLIC_COLUMNS = "id::text AS id, email, role, is_active, token_version, created_at"


class EmailTaken(ValueError):
    pass


def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(hash_: str, raw: str) -> bool:
    try:
        return _ph.verify(hash_, raw)
    except (VerifyMismatchError, Argon2Error, TypeError, ValueError):
        return False


def _norm(email: str) -> str:
    return email.strip().lower()


async def create(email: str, password: str, *, role: str = "user") -> dict[str, Any]:
    if role not in {"user", "admin"}:
        raise ValueError(f"unknown role {role!r}")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    async with connection() as conn:
        try:
            row = await (await conn.execute(
                f"INSERT INTO users (email, password_hash, role) VALUES (%s,%s,%s)"
                f" RETURNING {PUBLIC_COLUMNS}",
                (_norm(email), hash_password(password), role),
            )).fetchone()
        except psycopg.errors.UniqueViolation:
            raise EmailTaken(f"{_norm(email)} already has an account") from None
        await conn.commit()
    return row


async def by_id(user_id: str) -> dict[str, Any] | None:
    async with connection() as conn:
        return await (await conn.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM users WHERE id=%s", (user_id,)
        )).fetchone()


async def by_email(email: str) -> dict[str, Any] | None:
    async with connection() as conn:
        return await (await conn.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM users WHERE email=%s", (_norm(email),)
        )).fetchone()


async def authenticate(email: str, password: str) -> dict[str, Any] | None:
    async with connection() as conn:
        row = await (await conn.execute(
            f"SELECT {PUBLIC_COLUMNS}, password_hash FROM users WHERE email=%s",
            (_norm(email),),
        )).fetchone()
    if row is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(row["password_hash"], password):
        return None
    if not row["is_active"]:
        return None
    row.pop("password_hash")
    return row


async def set_active(user_id: str, active: bool) -> None:
    # Bumping the version on disable is the whole revocation mechanism: without
    # it a disabled user keeps working until their cookie happens to expire.
    async with connection() as conn:
        await conn.execute(
            "UPDATE users SET is_active=%s, token_version=token_version+1 WHERE id=%s",
            (active, user_id),
        )
        await conn.commit()


async def set_password(user_id: str, password: str) -> None:
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    async with connection() as conn:
        await conn.execute(
            "UPDATE users SET password_hash=%s, token_version=token_version+1"
            " WHERE id=%s",
            (hash_password(password), user_id),
        )
        await conn.commit()


async def set_role(user_id: str, role: str) -> None:
    if role not in {"user", "admin"}:
        raise ValueError(f"unknown role {role!r}")
    async with connection() as conn:
        await conn.execute("UPDATE users SET role=%s WHERE id=%s", (role, user_id))
        await conn.commit()


async def list_all() -> list[dict[str, Any]]:
    async with connection() as conn:
        return await (await conn.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM users ORDER BY created_at"
        )).fetchall()


async def count() -> int:
    async with connection() as conn:
        row = await (await conn.execute("SELECT COUNT(*) AS n FROM users")).fetchone()
    return int(row["n"])


async def ensure_bootstrap_admin(email: str | None,
                                 password: str | None) -> dict[str, Any] | None:
    """Create the first admin from the environment, once.

    Deploying to a fresh database otherwise leaves nobody able to log in and no
    shell to fix it from. Guarded on the table being empty rather than on the
    email being absent, so rotating ADMIN_PASSWORD later cannot silently mint a
    second admin.
    """
    if not email or not password:
        return None
    if await count() > 0:
        return None
    admin = await create(email, password, role="admin")
    log.info("created the first admin account (%s)", admin["email"])
    return admin
```

- [ ] **Step 5: Export it**

Add to `app/store/__init__.py`:

```python
from . import users  # noqa: F401
```

and append `"users"` to `__all__`.

- [ ] **Step 6: Run the user tests**

Run: `python -m pytest tests/test_users.py -v`
Expected: PASS (10 tests)

- [ ] **Step 7: Commit**

```bash
git add app/store/users.py app/store/__init__.py tests/test_users.py tests/conftest.py tests/test_migrations.py
git commit -m "Add user accounts with argon2 hashes and revocable sessions

token_version is what lets a stateless signed cookie be revoked: disabling a
user or changing their password bumps it, and every cookie already issued to
them stops validating on its next request. No session table needed.

authenticate() verifies against a dummy hash when the email is unknown, so a
missing account costs the same work as a wrong password and response time does
not enumerate who has an account."
```

---

### Task 4: Session cookies and the auth dependencies

**Files:**
- Create: `app/auth.py`
- Create: `app/routes/__init__.py`, `app/routes/auth.py`
- Create: `tests/test_auth.py`
- Create: `tests/factories.py`

**Interfaces:**
- Consumes: `app.store.users`, `app.settings.get_settings`.
- Produces:
  - `COOKIE_NAME = "h3_session"`
  - `issue(user: dict) -> str` — signed token carrying `{uid, tv}`
  - `read(token: str) -> tuple[str, int] | None` — `(user_id, token_version)` or None
  - `set_cookie(response, token) -> None`, `clear_cookie(response) -> None`
  - `async current_user(request) -> dict` — FastAPI dependency, raises 401
  - `async require_admin(user = Depends(current_user)) -> dict` — raises 403
  - `router` in `app/routes/auth.py` exposing `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/me`
  - `app.routes.auth.LoginBody(email: str, password: str)`
- Later tasks depend on `current_user` returning the public user dict (`id`, `email`, `role`, `is_active`, `token_version`, `created_at`).

- [ ] **Step 1: Write the test factory**

Create `tests/factories.py`:

```python
"""Helpers every route test needs: a running app and a logged-in client."""
from __future__ import annotations

import httpx

from app.store import users


async def make_user(email: str = "u@h3.local", password: str = "passphrase-1",
                    role: str = "user") -> dict:
    return await users.create(email, password, role=role)


async def login(client: httpx.AsyncClient, email: str,
                password: str = "passphrase-1") -> httpx.Response:
    return await client.post("/api/auth/login",
                             json={"email": email, "password": password})
```

- [ ] **Step 2: Write the failing auth tests**

Create `tests/test_auth.py`:

```python
from __future__ import annotations

import time

import pytest

from app import auth
from app.store import users
from app.settings import Settings


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("S3_BUCKET", "h3")
    monkeypatch.setenv("S3_ACCESS_KEY", "ak")
    monkeypatch.setenv("S3_SECRET_KEY", "sk")
    auth._serializer.cache_clear()
    yield
    auth._serializer.cache_clear()


def test_token_round_trip():
    token = auth.issue({"id": "abc", "token_version": 3})
    assert auth.read(token) == ("abc", 3)


def test_tampered_token_is_rejected():
    token = auth.issue({"id": "abc", "token_version": 1})
    assert auth.read(token[:-3] + "aaa") is None


def test_garbage_token_is_rejected():
    assert auth.read("not-a-token") is None
    assert auth.read("") is None


def test_expired_token_is_rejected(monkeypatch):
    token = auth.issue({"id": "abc", "token_version": 1})
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 60 * 60 * 24 * 400)
    assert auth.read(token) is None
```

Add route-level tests to the same file:

```python
async def test_login_sets_a_cookie(client, db):
    await users.create("a@b.c", "passphrase-1")
    r = await client.post("/api/auth/login",
                          json={"email": "a@b.c", "password": "passphrase-1"})
    assert r.status_code == 200
    assert auth.COOKIE_NAME in r.cookies
    me = await client.get("/api/me")
    assert me.json()["email"] == "a@b.c"


async def test_login_failure_is_indistinguishable(client, db):
    await users.create("a@b.c", "passphrase-1")
    wrong_pw = await client.post("/api/auth/login",
                                 json={"email": "a@b.c", "password": "nope"})
    no_user = await client.post("/api/auth/login",
                                json={"email": "zz@b.c", "password": "passphrase-1"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["detail"] == no_user.json()["detail"]


async def test_unauthenticated_request_is_401(client, db):
    assert (await client.get("/api/me")).status_code == 401


async def test_logout_clears_the_session(client, db):
    await users.create("a@b.c", "passphrase-1")
    await client.post("/api/auth/login",
                      json={"email": "a@b.c", "password": "passphrase-1"})
    await client.post("/api/auth/logout")
    assert (await client.get("/api/me")).status_code == 401


async def test_disabling_a_user_kills_their_live_session(client, db):
    u = await users.create("a@b.c", "passphrase-1")
    await client.post("/api/auth/login",
                      json={"email": "a@b.c", "password": "passphrase-1"})
    assert (await client.get("/api/me")).status_code == 200
    await users.set_active(u["id"], False)
    assert (await client.get("/api/me")).status_code == 401


async def test_health_needs_no_session(client, db):
    assert (await client.get("/api/health")).status_code == 200
```

- [ ] **Step 3: Add the `client` fixture**

Append to `tests/conftest.py`:

```python
import httpx
import pytest_asyncio


@pytest_asyncio.fixture
async def app_settings(monkeypatch, dsn):
    """Environment for an app instance wired to the test database and no GPU."""
    from app.settings import get_settings
    for k, v in {
        "DATABASE_URL": dsn,
        "SESSION_SECRET": "t" * 40,
        "S3_ENDPOINT": "http://localhost:1",
        "S3_BUCKET": "h3-test",
        "S3_ACCESS_KEY": "ak",
        "S3_SECRET_KEY": "sk",
        "MOCK": "true",
        "OUTPUT_SINK": "local",
        "COOKIE_SECURE": "false",
        "POD_POLICY": "off",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def client(db, app_settings):
    """An httpx client bound to the real ASGI app, cookies included."""
    from app.main import create_app
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c
```

`COOKIE_SECURE=false` matters: httpx will not store a `Secure` cookie sent over
`http://test`, so every logged-in test would silently be anonymous.

- [ ] **Step 4: Run and watch it fail**

Run: `python -m pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 5: Write `app/auth.py`**

```python
"""Sessions: a signed cookie, and the dependencies that guard every route.

Stateless rather than a session table, because the only thing a table would buy
here is revocation - and `users.token_version` buys that for one integer.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .settings import get_settings
from .store import users

COOKIE_NAME = "h3_session"
_SALT = "h3-studio-session-v1"


@lru_cache(maxsize=1)
def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=_SALT)


def issue(user: dict[str, Any]) -> str:
    return _serializer().dumps({"uid": str(user["id"]), "tv": user["token_version"]})


def read(token: str) -> tuple[str, int] | None:
    if not token:
        return None
    max_age = get_settings().session_max_age_days * 86400
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired, ValueError):
        return None
    uid, tv = data.get("uid"), data.get("tv")
    if not isinstance(uid, str) or not isinstance(tv, int):
        return None
    return uid, tv


def set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=s.session_max_age_days * 86400,
        httponly=True,            # unreadable from JavaScript, so XSS cannot steal it
        secure=s.cookie_secure,
        samesite="lax",           # blocks cross-site POSTs; the app is same-origin
        path="/",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


async def current_user(request: Request) -> dict[str, Any]:
    """Resolve the session, or 401.

    The token_version comparison is why a disabled account stops working at once
    rather than whenever its cookie happens to expire.
    """
    parsed = read(request.cookies.get(COOKIE_NAME, ""))
    if parsed is None:
        raise HTTPException(401, "not signed in")
    uid, tv = parsed
    user = await users.by_id(uid)
    if user is None or not user["is_active"] or user["token_version"] != tv:
        raise HTTPException(401, "not signed in")
    return user


async def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(403, "admins only")
    return user
```

`time` is imported so the expiry test can monkeypatch it; itsdangerous reads the
clock through `time.time`.

- [ ] **Step 6: Write the login routes with rate limiting**

Create `app/routes/__init__.py` (empty) and `app/routes/auth.py`:

```python
"""Sign in, sign out, and who am I."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth
from ..store import users

router = APIRouter(prefix="/api", tags=["auth"])

# In-process and therefore per-replica. That is sufficient here because the
# replica count is pinned at 1 for the orchestrator's sake anyway.
_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
WINDOW_SECONDS = 300
MAX_ATTEMPTS = 10


def _rate_limited(ip: str) -> bool:
    now = time.time()
    hits = _ATTEMPTS[ip]
    while hits and hits[0] < now - WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= MAX_ATTEMPTS:
        return True
    hits.append(now)
    return False


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(429, "too many sign-in attempts; wait five minutes")
    user = await users.authenticate(body.email, body.password)
    if user is None:
        # One message for every failure. Saying which half was wrong tells an
        # attacker which addresses have accounts.
        raise HTTPException(401, "wrong email or password")
    auth.set_cookie(response, auth.issue(user))
    return {"id": user["id"], "email": user["email"], "role": user["role"]}


@router.post("/auth/logout")
async def logout(response: Response) -> dict:
    auth.clear_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(auth.current_user)) -> dict:
    return {"id": user["id"], "email": user["email"], "role": user["role"]}
```

- [ ] **Step 7: Run the auth tests**

Run: `python -m pytest tests/test_auth.py -v`
Expected: the four pure-token tests PASS; the route tests FAIL with
`ModuleNotFoundError` on `app.main.create_app` until Task 9. That is expected —
mark them with `pytest.mark.xfail(reason="app factory arrives in Task 9")` now
and remove the marker in Task 9.

- [ ] **Step 8: Commit**

```bash
git add app/auth.py app/routes tests/test_auth.py tests/factories.py tests/conftest.py
git commit -m "Add signed-cookie sessions and the route guards

HttpOnly so XSS cannot read the cookie, SameSite=Lax so another site cannot POST
with it, Secure everywhere but local HTTP development.

Login answers with one message for every failure and verifies an unknown email
against a dummy hash, so neither the wording nor the timing says which addresses
have accounts."
```

---
### Task 5: The jobs store, scoped to a user

**Files:**
- Create: `app/store/jobs.py`, `app/store/runs.py`, `app/store/kv.py`
- Create: `tests/test_jobs_store.py`
- Modify: `app/store/__init__.py`

**Interfaces:**
- Consumes: `app.store.pool.connection`.
- Produces (`app.store.jobs`):
  - `async add(user_id, prompt, *, seconds=10, ref_images=(), seed=None, mode="i2v", preset="final") -> str`
  - `async list_for(user_id, limit=200) -> list[dict]`
  - `async get_for(user_id, job_id) -> dict | None` — **None when another user owns it**
  - `async get_any(job_id) -> dict | None` — orchestrator use only, never a route
  - `async update(job_id, **fields) -> None`
  - `async delete_for(user_id, job_id) -> bool`
  - `async claim_next_queued() -> dict | None`
  - `async counts_for(user_id) -> dict[str, int]`
  - `async counts_all() -> dict[str, int]`
  - `async queue_position(job_id) -> int` — how many queued jobs are ahead
  - `async archive_page(user_id, cursor: str | None, limit=24) -> tuple[list[dict], str | None]`
  - `async requeue_stuck_running() -> int`
  - `async list_all(limit=500) -> list[dict]` — admin only
- Produces (`app.store.runs`): `async start(pod_id, gpu_type, note="") -> str`, `async update(run_id, **fields)`, `async recent(limit=20) -> list[dict]`.
- Produces (`app.store.kv`): `async get(k, default=None) -> str | None`, `async set(k, v) -> None`.
- Every returned job dict has `ref_images` as a `list[str]` and timestamps as float epoch seconds, so the existing frontend and `estimate.py` need no change in shape.

- [ ] **Step 1: Write the failing store tests**

Create `tests/test_jobs_store.py`:

```python
from __future__ import annotations

import asyncio

from app.store import jobs, kv, runs, users


async def _two_users():
    a = await users.create("a@h3.local", "passphrase-1")
    b = await users.create("b@h3.local", "passphrase-2")
    return a, b


async def test_add_and_list_is_scoped(db):
    a, b = await _two_users()
    await jobs.add(a["id"], "a's clip")
    await jobs.add(b["id"], "b's clip")
    mine = await jobs.list_for(a["id"])
    assert [j["prompt"] for j in mine] == ["a's clip"]


async def test_get_for_refuses_another_users_job(db):
    a, b = await _two_users()
    job_id = await jobs.add(a["id"], "private")
    assert await jobs.get_for(a["id"], job_id) is not None
    assert await jobs.get_for(b["id"], job_id) is None


async def test_delete_is_scoped(db):
    a, b = await _two_users()
    job_id = await jobs.add(a["id"], "private")
    assert await jobs.delete_for(b["id"], job_id) is False
    assert await jobs.get_for(a["id"], job_id) is not None
    assert await jobs.delete_for(a["id"], job_id) is True


async def test_ref_images_round_trip_as_a_list(db):
    a, _ = await _two_users()
    job_id = await jobs.add(a["id"], "p", ref_images=["uploads/x/1.png"])
    assert (await jobs.get_for(a["id"], job_id))["ref_images"] == ["uploads/x/1.png"]
    await jobs.update(job_id, ref_images=["uploads/x/2.png"])
    assert (await jobs.get_for(a["id"], job_id))["ref_images"] == ["uploads/x/2.png"]


async def test_claim_hands_each_job_to_exactly_one_caller(db):
    a, _ = await _two_users()
    for i in range(20):
        await jobs.add(a["id"], f"clip {i}")
    claimed = await asyncio.gather(*[jobs.claim_next_queued() for _ in range(30)])
    ids = [c["id"] for c in claimed if c]
    assert len(ids) == 20
    assert len(set(ids)) == 20


async def test_claim_is_fifo(db):
    a, _ = await _two_users()
    first = await jobs.add(a["id"], "first")
    await jobs.add(a["id"], "second")
    assert (await jobs.claim_next_queued())["id"] == first


async def test_counts_are_scoped(db):
    a, b = await _two_users()
    await jobs.add(a["id"], "one")
    await jobs.add(a["id"], "two")
    await jobs.add(b["id"], "three")
    assert (await jobs.counts_for(a["id"]))["queued"] == 2
    assert (await jobs.counts_all())["queued"] == 3


async def test_queue_position_counts_only_what_is_ahead(db):
    a, _ = await _two_users()
    await jobs.add(a["id"], "first")
    mine = await jobs.add(a["id"], "second")
    assert await jobs.queue_position(mine) == 1


async def test_archive_pages_forward_without_repeating(db):
    a, _ = await _two_users()
    for i in range(5):
        jid = await jobs.add(a["id"], f"clip {i}")
        await jobs.update(jid, status="done", finished_at=None,
                          output_key=f"videos/{a['id']}/{jid}.mp4")
        await jobs.update(jid, finished_at=__import__("time").time() + i)
    page1, cur = await jobs.archive_page(a["id"], None, limit=2)
    page2, cur2 = await jobs.archive_page(a["id"], cur, limit=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {j["id"] for j in page1}.isdisjoint({j["id"] for j in page2})
    assert cur2 is not None


async def test_archive_excludes_other_users(db):
    a, b = await _two_users()
    jid = await jobs.add(b["id"], "b's clip")
    await jobs.update(jid, status="done", output_key="k", finished_at=1.0)
    page, _ = await jobs.archive_page(a["id"], None)
    assert page == []


async def test_requeue_stuck_running(db):
    a, _ = await _two_users()
    jid = await jobs.add(a["id"], "orphan")
    await jobs.update(jid, status="running", remote_id="r1")
    assert await jobs.requeue_stuck_running() == 1
    row = await jobs.get_for(a["id"], jid)
    assert row["status"] == "queued" and row["remote_id"] is None


async def test_kv_round_trip(db):
    assert await kv.get("policy") is None
    await kv.set("policy", "auto")
    await kv.set("policy", "off")
    assert await kv.get("policy") == "off"


async def test_runs_record_a_session(db):
    rid = await runs.start("pod-1", "RTX 5090", note="test")
    await runs.update(rid, status="stopped", cost_estimate=1.25)
    rows = await runs.recent()
    assert rows[0]["id"] == rid and float(rows[0]["cost_estimate"]) == 1.25
```

- [ ] **Step 2: Run and watch it fail**

Run: `python -m pytest tests/test_jobs_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'jobs' from 'app.store'`

- [ ] **Step 3: Write `app/store/jobs.py`**

```python
"""Job rows. Every read a route can reach is scoped to one user.

Two functions read a job: `get_for(user_id, job_id)` and `get_any(job_id)`. Routes
may only call the first. The second exists for the orchestrator, which processes
the global queue and legitimately has no user in hand - keeping them as separate
names means an unscoped read is visible at the call site rather than hidden in an
optional argument that someone will eventually leave out.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from .pool import connection

STATUSES = ("queued", "running", "done", "failed", "cancelled")

# Timestamps leave as float epoch seconds because the frontend, estimate.py and
# the orchestrator all already speak that; converting at the boundary is cheaper
# than changing three consumers.
COLUMNS = """
    id, user_id::text AS user_id, status, prompt, ref_images, seconds, seed, mode,
    preset,
    EXTRACT(EPOCH FROM created_at)  AS created_at,
    EXTRACT(EPOCH FROM started_at)  AS started_at,
    EXTRACT(EPOCH FROM finished_at) AS finished_at,
    attempts, error, output_key, output_bytes, remote_id
"""

_TIMESTAMP_FIELDS = {"started_at", "finished_at"}


def _shape(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    for k in ("created_at", "started_at", "finished_at"):
        if row.get(k) is not None:
            row[k] = float(row[k])
    return row


def _encode(fields: dict[str, Any]) -> dict[str, Any]:
    """Translate the callers' vocabulary into columns Postgres accepts."""
    out = dict(fields)
    if "ref_images" in out and not isinstance(out["ref_images"], str):
        out["ref_images"] = json.dumps(list(out["ref_images"]))
    for k in _TIMESTAMP_FIELDS:
        if k in out and isinstance(out[k], (int, float)):
            out[k] = datetime.fromtimestamp(out[k], tz=timezone.utc)
    return out


async def add(user_id: str, prompt: str, *, seconds: int = 10,
              ref_images: Iterable[str] = (), seed: int | None = None,
              mode: str = "i2v", preset: str = "final") -> str:
    job_id = uuid.uuid4().hex[:12]
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, user_id, prompt, ref_images, seconds, seed, mode,"
            " preset) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s)",
            (job_id, user_id, prompt, json.dumps(list(ref_images)), seconds, seed,
             mode, preset),
        )
        await conn.commit()
    return job_id


async def list_for(user_id: str, limit: int = 200) -> list[dict[str, Any]]:
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT {COLUMNS} FROM jobs WHERE user_id=%s"
            " ORDER BY created_at DESC LIMIT %s", (user_id, limit)
        )).fetchall()
    return [_shape(r) for r in rows]


async def list_all(limit: int = 500) -> list[dict[str, Any]]:
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT {COLUMNS}, (SELECT email FROM users u WHERE u.id = jobs.user_id)"
            f" AS user_email FROM jobs ORDER BY created_at DESC LIMIT %s", (limit,)
        )).fetchall()
    return [_shape(r) for r in rows]


async def get_for(user_id: str, job_id: str) -> dict[str, Any] | None:
    async with connection() as conn:
        return _shape(await (await conn.execute(
            f"SELECT {COLUMNS} FROM jobs WHERE id=%s AND user_id=%s", (job_id, user_id)
        )).fetchone())


async def get_any(job_id: str) -> dict[str, Any] | None:
    async with connection() as conn:
        return _shape(await (await conn.execute(
            f"SELECT {COLUMNS} FROM jobs WHERE id=%s", (job_id,)
        )).fetchone())


async def update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    enc = _encode(fields)
    casts = {"ref_images": "%s::jsonb"}
    sets = ", ".join(f"{k}={casts.get(k, '%s')}" for k in enc)
    async with connection() as conn:
        await conn.execute(f"UPDATE jobs SET {sets} WHERE id=%s",
                           (*enc.values(), job_id))
        await conn.commit()


async def delete_for(user_id: str, job_id: str) -> bool:
    async with connection() as conn:
        cur = await conn.execute("DELETE FROM jobs WHERE id=%s AND user_id=%s",
                                 (job_id, user_id))
        await conn.commit()
    return cur.rowcount > 0


async def clear_finished_for(user_id: str) -> int:
    async with connection() as conn:
        cur = await conn.execute(
            "DELETE FROM jobs WHERE user_id=%s AND status IN ('done','cancelled')",
            (user_id,))
        await conn.commit()
    return cur.rowcount


async def claim_next_queued() -> dict[str, Any] | None:
    """Take the oldest queued job, atomically.

    SKIP LOCKED rather than the SQLite version's BEGIN IMMEDIATE: it is the
    Postgres idiom for a work queue and it does not make a second claimer wait on
    the first, which matters because the caller is an event loop.
    """
    async with connection() as conn:
        row = await (await conn.execute(f"""
            UPDATE jobs SET status='running', started_at=now(), attempts=attempts+1
            WHERE id = (SELECT id FROM jobs WHERE status='queued'
                        ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING {COLUMNS}
        """)).fetchone()
        await conn.commit()
    return _shape(row)


async def _counts(where: str, params: tuple) -> dict[str, int]:
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT status, COUNT(*) AS c FROM jobs {where} GROUP BY status", params
        )).fetchall()
    out = {s: 0 for s in STATUSES}
    for r in rows:
        out[r["status"]] = int(r["c"])
    return out


async def counts_for(user_id: str) -> dict[str, int]:
    return await _counts("WHERE user_id=%s", (user_id,))


async def counts_all() -> dict[str, int]:
    return await _counts("", ())


async def queue_position(job_id: str) -> int:
    """How many queued jobs sit ahead of this one.

    A number only. Showing a user that three clips are in front of theirs is the
    difference between 'waiting through a cold boot' and 'broken'; showing them
    whose clips those are is not.
    """
    async with connection() as conn:
        row = await (await conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status='queued' AND created_at <"
            " (SELECT created_at FROM jobs WHERE id=%s)", (job_id,)
        )).fetchone()
    return int(row["n"])


def _cursor_encode(finished_at: float, job_id: str) -> str:
    return base64.urlsafe_b64encode(f"{finished_at}|{job_id}".encode()).decode()


def _cursor_decode(cursor: str) -> tuple[float, str] | None:
    try:
        ts, jid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return float(ts), jid
    except (ValueError, UnicodeDecodeError):
        return None


async def archive_page(user_id: str, cursor: str | None,
                       limit: int = 24) -> tuple[list[dict[str, Any]], str | None]:
    """Finished clips, newest first, keyset-paginated.

    Keyset rather than OFFSET: a clip finishing while the user scrolls would shift
    every later page under an offset, showing one row twice and skipping another.
    """
    limit = max(1, min(100, limit))
    params: list[Any] = [user_id]
    extra = ""
    if cursor and (decoded := _cursor_decode(cursor)):
        ts, jid = decoded
        extra = (" AND (EXTRACT(EPOCH FROM finished_at), id) <"
                 " (%s, %s)")
        params += [ts, jid]
    params.append(limit + 1)
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT {COLUMNS} FROM jobs WHERE user_id=%s AND status='done'"
            f" AND output_key IS NOT NULL{extra}"
            f" ORDER BY finished_at DESC, id DESC LIMIT %s", tuple(params)
        )).fetchall()
    rows = [_shape(r) for r in rows]
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = _cursor_encode(last["finished_at"] or 0.0, last["id"])
    return rows, next_cursor


async def requeue_stuck_running() -> int:
    """Anything left 'running' by a previous process is orphaned, not in flight."""
    async with connection() as conn:
        cur = await conn.execute(
            "UPDATE jobs SET status='queued', remote_id=NULL WHERE status='running'")
        await conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: Write `app/store/runs.py`**

```python
"""Pod sessions, so the cost of every boot is on record."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .pool import connection

COLUMNS = """
    id, pod_id, status, endpoint, gpu_type,
    EXTRACT(EPOCH FROM started_at) AS started_at,
    EXTRACT(EPOCH FROM ended_at)   AS ended_at,
    cost_estimate, note
"""


async def start(pod_id: str | None, gpu_type: str, note: str = "") -> str:
    run_id = uuid.uuid4().hex[:12]
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO runs (id, pod_id, status, gpu_type, note)"
            " VALUES (%s,%s,'booting',%s,%s)", (run_id, pod_id, gpu_type, note))
        await conn.commit()
    return run_id


async def update(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    enc = dict(fields)
    if isinstance(enc.get("ended_at"), (int, float)):
        enc["ended_at"] = datetime.fromtimestamp(enc["ended_at"], tz=timezone.utc)
    sets = ", ".join(f"{k}=%s" for k in enc)
    async with connection() as conn:
        await conn.execute(f"UPDATE runs SET {sets} WHERE id=%s",
                           (*enc.values(), run_id))
        await conn.commit()


async def recent(limit: int = 20) -> list[dict[str, Any]]:
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT {COLUMNS} FROM runs ORDER BY started_at DESC LIMIT %s", (limit,)
        )).fetchall()
    for r in rows:
        r["cost_estimate"] = float(r["cost_estimate"])
    return rows
```

- [ ] **Step 5: Write `app/store/kv.py`**

```python
"""Settings an admin changes at runtime.

These live in the database rather than in a file because the container filesystem
is wiped on every redeploy, and 'the GPU policy reset itself when we shipped a CSS
fix' is the kind of surprise that costs money.
"""
from __future__ import annotations

from .pool import connection


async def get(k: str, default: str | None = None) -> str | None:
    async with connection() as conn:
        row = await (await conn.execute("SELECT v FROM kv WHERE k=%s", (k,))).fetchone()
    return row["v"] if row else default


async def set(k: str, v: str) -> None:  # noqa: A001 - the table is called kv
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO kv (k,v) VALUES (%s,%s)"
            " ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (k, v))
        await conn.commit()
```

- [ ] **Step 6: Export the modules**

`app/store/__init__.py` gains `from . import jobs, kv, runs, users  # noqa: F401`
and the names in `__all__`.

- [ ] **Step 7: Run the store tests**

Run: `python -m pytest tests/test_jobs_store.py -v`
Expected: PASS (13 tests)

- [ ] **Step 8: Commit**

```bash
git add app/store tests/test_jobs_store.py
git commit -m "Scope every job read to its owner

get_for(user_id, job_id) and get_any(job_id) are separate names rather than one
function with an optional user, so an unscoped read is visible at the call site
instead of hidden in an argument someone eventually forgets. Routes may only
call the first.

The queue claim moves to FOR UPDATE SKIP LOCKED, which does not make a second
claimer wait on the first - the caller is an event loop."
```

---

### Task 6: S3 storage and the S3 output sink

**Files:**
- Create: `app/storage.py`, `app/sinks/s3.py`
- Modify: `app/sinks/__init__.py` (`make_sink` chooses by settings; `strip_audio` works on bytes)
- Create: `tests/test_storage.py`, `tests/test_s3_sink.py`

**Interfaces:**
- Consumes: `app.settings.get_settings`.
- Produces (`app.storage`):
  - `class Storage` with `async put(key: str, data: bytes, content_type: str) -> int`, `async get(key: str) -> bytes`, `async head(key: str) -> dict | None`, `async stream(key: str, byte_range: str | None) -> tuple[Iterator[bytes], int, str | None]`, `async delete(key: str) -> None`, `async ensure_bucket() -> None`
  - `get_storage() -> Storage` (cached)
  - `class ObjectMissing(KeyError)`
  - `video_key(user_id, job_id, slug) -> str`, `upload_key(user_id, ext) -> str`
- Produces (`app.sinks.s3`): `class S3Sink` with `name = "s3"` and `async put(job, data, filename) -> str` returning the S3 key.
- `make_sink(settings)` returns `S3Sink` when `output_sink == "s3"`, else `LocalFolderSink`.

- [ ] **Step 1: Write the failing storage tests**

Create `tests/test_storage.py`:

```python
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from app import storage
from app.settings import get_settings


@pytest.fixture
def s3(monkeypatch):
    for k, v in {
        "DATABASE_URL": "postgresql://u:p@h/db",
        "SESSION_SECRET": "s" * 40,
        "S3_ENDPOINT": "",              # moto intercepts the real endpoint
        "S3_BUCKET": "h3-test",
        "S3_ACCESS_KEY": "testing",
        "S3_SECRET_KEY": "testing",
        "S3_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    storage.get_storage.cache_clear()
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="h3-test")
        yield storage.get_storage()
    get_settings.cache_clear()
    storage.get_storage.cache_clear()


async def test_put_then_get(s3):
    n = await s3.put("videos/u1/j1.mp4", b"hello", "video/mp4")
    assert n == 5
    assert await s3.get("videos/u1/j1.mp4") == b"hello"


async def test_get_missing_raises_object_missing(s3):
    with pytest.raises(storage.ObjectMissing):
        await s3.get("videos/u1/nope.mp4")


async def test_head_reports_size_and_type(s3):
    await s3.put("videos/u1/j1.mp4", b"x" * 100, "video/mp4")
    meta = await s3.head("videos/u1/j1.mp4")
    assert meta["size"] == 100 and meta["content_type"] == "video/mp4"
    assert await s3.head("videos/u1/nope.mp4") is None


async def test_stream_whole_object(s3):
    await s3.put("k", b"abcdefghij", "video/mp4")
    chunks, size, content_range = await s3.stream("k", None)
    assert b"".join(chunks) == b"abcdefghij"
    assert size == 10 and content_range is None


async def test_stream_honours_a_range_header(s3):
    await s3.put("k", b"abcdefghij", "video/mp4")
    chunks, size, content_range = await s3.stream("k", "bytes=2-5")
    assert b"".join(chunks) == b"cdef"
    assert size == 4
    assert content_range == "bytes 2-5/10"


async def test_delete(s3):
    await s3.put("k", b"x", "video/mp4")
    await s3.delete("k")
    assert await s3.head("k") is None


def test_keys_are_namespaced_by_user():
    assert storage.video_key("u1", "j1", "a-clip") == "videos/u1/j1_a-clip.mp4"
    key = storage.upload_key("u1", ".png")
    assert key.startswith("uploads/u1/") and key.endswith(".png")
```

- [ ] **Step 2: Run and watch it fail**

Run: `python -m pytest tests/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.storage'`

- [ ] **Step 3: Write `app/storage.py`**

```python
"""The object store: finished clips and uploaded reference images.

boto3 is synchronous, so every call is pushed to a thread. The alternative -
an async S3 client - would add a dependency to save a thread hop on a path that
is already dominated by moving tens of megabytes over a socket.

The bucket is private. Nothing here ever mints a public or presigned URL; the app
reads objects and streams them to the browser after checking the database says
that user owns the job. Keys carry the user id for legibility, never for
authorization.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from functools import lru_cache
from typing import Any, Iterator

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .settings import get_settings

log = logging.getLogger("h3studio.storage")

CHUNK = 1024 * 256
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class ObjectMissing(KeyError):
    pass


def video_key(user_id: str, job_id: str, slug: str) -> str:
    return f"videos/{user_id}/{job_id}_{slug}.mp4"


def upload_key(user_id: str, ext: str) -> str:
    if not ext.startswith("."):
        ext = "." + ext
    return f"uploads/{user_id}/{uuid.uuid4().hex[:16]}{ext}"


class Storage:
    def __init__(self, client: Any, bucket: str) -> None:
        self._c = client
        self.bucket = bucket

    async def ensure_bucket(self) -> None:
        def _go() -> None:
            try:
                self._c.head_bucket(Bucket=self.bucket)
            except ClientError:
                log.info("creating bucket %s", self.bucket)
                self._c.create_bucket(Bucket=self.bucket)
        await asyncio.to_thread(_go)

    async def put(self, key: str, data: bytes, content_type: str) -> int:
        await asyncio.to_thread(
            self._c.put_object, Bucket=self.bucket, Key=key, Body=data,
            ContentType=content_type)
        return len(data)

    async def get(self, key: str) -> bytes:
        def _go() -> bytes:
            try:
                return self._c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except ClientError as e:
                raise ObjectMissing(key) from e
        return await asyncio.to_thread(_go)

    async def head(self, key: str) -> dict[str, Any] | None:
        def _go() -> dict[str, Any] | None:
            try:
                r = self._c.head_object(Bucket=self.bucket, Key=key)
            except ClientError:
                return None
            return {"size": int(r["ContentLength"]),
                    "content_type": r.get("ContentType", "application/octet-stream")}
        return await asyncio.to_thread(_go)

    async def stream(self, key: str, byte_range: str | None
                     ) -> tuple[Iterator[bytes], int, str | None]:
        """Return an iterator of chunks, the byte count, and a Content-Range.

        Range support is not decoration: without it a browser cannot seek in an
        MP4 served from here, so scrubbing a ten-second clip means downloading it
        again from the start.
        """
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if byte_range and _RANGE_RE.match(byte_range):
            kwargs["Range"] = byte_range

        def _go():
            try:
                return self._c.get_object(**kwargs)
            except ClientError as e:
                raise ObjectMissing(key) from e

        r = await asyncio.to_thread(_go)
        body = r["Body"]
        size = int(r["ContentLength"])
        content_range = r.get("ContentRange")

        def _chunks() -> Iterator[bytes]:
            try:
                while chunk := body.read(CHUNK):
                    yield chunk
            finally:
                body.close()

        return _chunks(), size, content_range

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._c.delete_object, Bucket=self.bucket, Key=key)


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    s = get_settings()
    client = boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint or None,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
        # MinIO and most self-hosted S3 do not resolve virtual-host style
        # bucket.endpoint names; path style works on both.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return Storage(client, s.s3_bucket)
```

- [ ] **Step 4: Run the storage tests**

Run: `python -m pytest tests/test_storage.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Write the failing sink test**

Create `tests/test_s3_sink.py`:

```python
from __future__ import annotations

import shutil

import pytest

from app.sinks.s3 import S3Sink


async def test_put_stores_under_the_owner(s3):
    sink = S3Sink(s3, keep_audio=True)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "a red car"},
                         b"video-bytes", "clip.mp4")
    assert key == "videos/u1/j1_a-red-car.mp4"
    assert await s3.get(key) == b"video-bytes"


async def test_two_jobs_never_collide(s3):
    sink = S3Sink(s3, keep_audio=True)
    a = await sink.put({"id": "j1", "user_id": "u1", "prompt": "same"}, b"1", "c.mp4")
    b = await sink.put({"id": "j2", "user_id": "u1", "prompt": "same"}, b"2", "c.mp4")
    assert a != b
    assert await s3.get(a) == b"1" and await s3.get(b) == b"2"


async def test_prompt_with_no_usable_characters_still_gets_a_key(s3):
    sink = S3Sink(s3, keep_audio=True)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "!!!"}, b"x", "c.mp4")
    assert key == "videos/u1/j1_clip.mp4"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_audio_is_stripped_before_upload(s3, tmp_path):
    import subprocess
    src = tmp_path / "in.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1:r=8",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(src)], check=True)
    sink = S3Sink(s3, keep_audio=False)
    key = await sink.put({"id": "j1", "user_id": "u1", "prompt": "p"},
                         src.read_bytes(), "c.mp4")
    out = await s3.get(key)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", "-"],
        input=out, capture_output=True)
    assert probe.stdout.strip() == b""
```

Move the `s3` fixture from `tests/test_storage.py` into `tests/conftest.py` so
both files share it.

- [ ] **Step 6: Run and watch it fail**

Run: `python -m pytest tests/test_s3_sink.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sinks.s3'`

- [ ] **Step 7: Make `strip_audio` work on bytes**

In `app/sinks/__init__.py`, add alongside the existing path-based `strip_audio`:

```python
def strip_audio_bytes(data: bytes) -> bytes:
    """Drop the audio stream, returning new bytes.

    Under a step-distilled LoRA the audio H3 emits is undenoised noise, measured
    at -13.9 dB flat against -34.8 dB for the same prompt at thirty steps. `-c copy`
    remuxes rather than re-encodes, so this costs milliseconds and cannot degrade
    the picture.

    Any failure returns the input untouched: a clip with unwanted audio is a far
    better outcome than no clip, on something the user has already paid for.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot strip audio: ffmpeg is not on PATH")
        return data
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "out.mp4"
        src.write_bytes(data)
        try:
            r = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", str(src),
                 "-map", "0:v", "-c", "copy", "-an", str(dst)],
                capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("audio strip failed: %s", e)
            return data
        # Trust the file, not the exit code: ffmpeg can return 0 having written
        # nothing usable, and replacing a good clip with an empty one is the one
        # failure that would actually lose work.
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
            log.warning("audio strip produced nothing usable; keeping the original")
            return data
        return dst.read_bytes()
```

Add `import tempfile` to that module's imports.

- [ ] **Step 8: Write `app/sinks/s3.py`**

```python
"""Finished clips into the object store.

Implements the same OutputSink protocol the local folder sink does, which is why
the orchestrator does not change: it hands over bytes and gets back a locator.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import storage as storage_mod
from . import slugify, strip_audio_bytes


class S3Sink:
    name = "s3"

    def __init__(self, store: storage_mod.Storage, *, keep_audio: bool = True) -> None:
        self._store = store
        self.keep_audio = keep_audio

    async def put(self, job: dict[str, Any], data: bytes, filename: str) -> str:
        if not self.keep_audio:
            data = await asyncio.to_thread(strip_audio_bytes, data)
        key = storage_mod.video_key(
            str(job["user_id"]), str(job["id"]), slugify(job.get("prompt", "")))
        await self._store.put(key, data, "video/mp4")
        return key
```

The job id is already unique, so the key is unique without the local sink's
`_2` suffix loop, and re-running a prompt cannot overwrite an earlier take.

- [ ] **Step 9: Point `make_sink` at settings**

Replace `make_sink` in `app/sinks/__init__.py`:

```python
def make_sink(settings: Any) -> OutputSink:
    """Choose where finished clips go.

    'local' exists for tests and for running the mock backend without an object
    store; production is always 's3'.
    """
    if settings.output_sink == "s3":
        from .. import storage as storage_mod
        from .s3 import S3Sink
        return S3Sink(storage_mod.get_storage(), keep_audio=settings.keep_audio)
    return LocalFolderSink(settings.local_output_folder,
                           keep_audio=settings.keep_audio)
```

- [ ] **Step 10: Run the sink tests**

Run: `python -m pytest tests/test_s3_sink.py tests/test_storage.py -v`
Expected: PASS (11 tests, one skipped without ffmpeg)

- [ ] **Step 11: Commit**

```bash
git add app/storage.py app/sinks tests/test_storage.py tests/test_s3_sink.py tests/conftest.py
git commit -m "Store finished clips in S3, streamed back through the app

The bucket stays private and nothing mints a presigned URL: the app reads the
object and streams it after the database confirms the requester owns the job.
Keys carry the user id so an object's owner is legible, but authorization is
always decided from the row, never parsed from the key.

Range requests are forwarded, without which a browser cannot seek in an MP4
served from here - scrubbing would re-download the clip from the start."
```

---

### Task 7: Hand reference images to backends as bytes

**Files:**
- Modify: `app/backends/__init__.py` (protocol), `app/backends/comfy.py:51`, `app/backends/runpod_pod.py:467`, `app/backends/mock.py:104`
- Create: `tests/test_backend_upload.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Backend.upload_image(self, data: bytes, name: str) -> str` replaces `upload_image(self, local_path: Path) -> str` on all three backends and on `ComfyClient`.
- Task 8's orchestrator calls it with bytes fetched from S3.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backend_upload.py`:

```python
from __future__ import annotations

import inspect

from app.backends import Backend
from app.backends.comfy import ComfyClient
from app.backends.mock import MockBackend
from app.backends.runpod_pod import RunpodBackend


def test_every_backend_takes_bytes():
    """Reference images live in S3 now; nothing may assume a local path."""
    for cls in (MockBackend, RunpodBackend, ComfyClient):
        params = list(inspect.signature(cls.upload_image).parameters)
        assert params[:3] == ["self", "data", "name"], cls.__name__


async def test_mock_returns_the_name_it_was_given():
    m = MockBackend(cfg=None)
    assert await m.upload_image(b"\x89PNG", "ref.png") == "ref.png"
```

- [ ] **Step 2: Run and watch it fail**

Run: `python -m pytest tests/test_backend_upload.py -v`
Expected: FAIL — `assert ['self', 'local_path'] == ['self', 'data', 'name']`

- [ ] **Step 3: Change the protocol**

In `app/backends/__init__.py`, replace the `upload_image` entry:

```python
    async def upload_image(self, data: bytes, name: str) -> str:
        """Push a reference image; returns the name the workflow should reference.

        Bytes rather than a path: the image lives in the object store, and the
        container that renders it is not the container that received the upload.
        """
```

- [ ] **Step 4: Change `ComfyClient.upload_image`**

Replace `app/backends/comfy.py:51-60` with:

```python
    async def upload_image(self, data: bytes, name: str, *,
                           overwrite: bool = True) -> str:
        files = {"image": (name, data, "application/octet-stream")}
        form = {"overwrite": "true" if overwrite else "false", "type": "input"}
        r = await self._http.post(f"{self.endpoint}/upload/image", files=files,
                                  data=form)
        r.raise_for_status()
        payload = r.json()
        got = payload.get("name") or name
        sub = payload.get("subfolder") or ""
        return f"{sub}/{got}" if sub else got
```

- [ ] **Step 5: Change `RunpodBackend.upload_image`**

Replace `app/backends/runpod_pod.py:467-470` with:

```python
    async def upload_image(self, data: bytes, name: str) -> str:
        if not self._comfy:
            raise RuntimeError("pod not ready")
        return await self._comfy.upload_image(data, name)
```

- [ ] **Step 6: Change `MockBackend.upload_image`**

Replace `app/backends/mock.py:104-105` with:

```python
    async def upload_image(self, data: bytes, name: str) -> str:
        return name
```

- [ ] **Step 7: Drop the now-unused `Path` imports**

Run: `python -m pyflakes app/backends/*.py` (or read the files) and remove
`from pathlib import Path` where it is no longer referenced. `mock.py` still uses
`Path` for font lookup; `runpod_pod.py` and `comfy.py` may not.

- [ ] **Step 8: Run the test**

Run: `python -m pytest tests/test_backend_upload.py -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Commit**

```bash
git add app/backends tests/test_backend_upload.py
git commit -m "Take reference images as bytes, not a local path

The image is in the object store now, and the process that received the upload
is not necessarily the one still running when the job is dispatched. A path
parameter would have quietly worked in development and failed on the first
redeploy."
```

---
### Task 8: Orchestrator on the async store, behind an advisory lock

**Files:**
- Modify: `app/orchestrator.py` (whole file)
- Modify: `app/config.py` (drop `save()`, drop file loading, take values from `Settings`)
- Delete: `app/db.py`
- Create: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `app.store.jobs`, `app.store.runs`, `app.store.kv`, `app.store.pool`, `app.storage`, `app.sinks.make_sink`, `app.settings.get_settings`.
- Produces:
  - `Orchestrator(cfg, backend, sink, storage)` — dependencies injected rather than constructed inside, so tests can pass fakes.
  - `async start() -> None` — takes the advisory lock; sets `self.leader`
  - `async stop() -> None`
  - `async snapshot() -> dict` — now async, because counts come from the database
  - `async snapshot_for(user_id) -> dict` — the per-user view the status route returns
  - `async policy() -> str`, `async set_policy(value: str) -> None`
  - Attribute `leader: bool`

- [ ] **Step 1: Write the failing orchestrator tests**

Create `tests/test_orchestrator.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from app.backends import JobResult, PodStatus
from app.orchestrator import Orchestrator
from app.store import jobs, users


class FakeBackend:
    """Deterministic stand-in: no timers, no ffmpeg, no money."""
    name = "fake"

    def __init__(self, *, fail_times: int = 0) -> None:
        self.cfg = None
        self.up = False
        self.submitted: list[dict] = []
        self.uploaded: list[tuple[bytes, str]] = []
        self.fail_times = fail_times
        self.shutdowns = 0
        self._cost = 0.0

    def cost_so_far(self) -> float:
        return self._cost

    @property
    def rate_per_hour(self) -> float:
        return 1.0

    @property
    def gpu_used(self) -> str:
        return "fake-gpu"

    async def status(self) -> PodStatus:
        return PodStatus(state="ready" if self.up else "off", pod_id="fake")

    async def ensure_ready(self) -> PodStatus:
        self.up = True
        return PodStatus(state="ready", pod_id="fake", endpoint="http://fake")

    async def shutdown(self) -> None:
        self.up = False
        self.shutdowns += 1

    async def upload_image(self, data: bytes, name: str) -> str:
        self.uploaded.append((data, name))
        return name

    async def submit(self, job: dict) -> str:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("submit blew up")
        self.submitted.append(job)
        return f"remote-{job['id']}"

    async def poll(self, remote_id: str) -> JobResult:
        return JobResult(state="done", video=b"mp4-bytes", filename="c.mp4")

    async def aclose(self) -> None:
        return None


class FakeSink:
    def __init__(self) -> None:
        self.saved: list[tuple[str, bytes]] = []

    async def put(self, job, data, filename) -> str:
        key = f"videos/{job['user_id']}/{job['id']}.mp4"
        self.saved.append((key, data))
        return key


class FakeStorage:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}

    async def get(self, key: str) -> bytes:
        return self.objects[key]


def _cfg(app_settings):
    from app.config import Config
    return Config.from_settings(app_settings)


async def _orch(app_settings, backend=None, sink=None, storage=None):
    o = Orchestrator(_cfg(app_settings), backend or FakeBackend(),
                     sink or FakeSink(), storage or FakeStorage())
    await o.start()
    return o


async def test_becomes_leader_and_drains_the_queue(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "a clip")
    backend, sink = FakeBackend(), FakeSink()
    o = await _orch(app_settings, backend, sink)
    try:
        assert o.leader is True
        await o.set_policy("auto")
        for _ in range(10):
            await o._tick()
            if (await jobs.get_any(jid))["status"] == "done":
                break
        row = await jobs.get_any(jid)
        assert row["status"] == "done"
        assert row["output_key"] == f"videos/{u['id']}/{jid}.mp4"
        assert sink.saved and sink.saved[0][1] == b"mp4-bytes"
    finally:
        await o.stop()


async def test_a_second_orchestrator_is_not_leader(db, app_settings):
    first = await _orch(app_settings)
    try:
        second = Orchestrator(_cfg(app_settings), FakeBackend(), FakeSink(),
                              FakeStorage())
        await second.start()
        assert second.leader is False
        await second.stop()
        # A follower must never bring a pod up.
        assert second.backend.up is False
    finally:
        await first.stop()


async def test_policy_off_tears_the_pod_down(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    await jobs.add(u["id"], "a clip")
    backend = FakeBackend()
    o = await _orch(app_settings, backend)
    try:
        await o.set_policy("auto")
        await o._tick()
        assert backend.up is True
        await o.set_policy("off")
        await o._tick()
        assert backend.up is False
    finally:
        await o.stop()


async def test_a_job_that_keeps_failing_stops_after_three_attempts(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "doomed")
    o = await _orch(app_settings, FakeBackend(fail_times=99))
    try:
        await o.set_policy("auto")
        for _ in range(6):
            await o._tick()
        row = await jobs.get_any(jid)
        assert row["status"] == "failed"
        assert row["attempts"] == 3
    finally:
        await o.stop()


async def test_reference_images_are_fetched_from_storage(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    key = f"uploads/{u['id']}/ref.png"
    jid = await jobs.add(u["id"], "with a ref", ref_images=[key])
    backend = FakeBackend()
    o = await _orch(app_settings, backend, storage=FakeStorage({key: b"PNGDATA"}))
    try:
        await o.set_policy("auto")
        await o._tick()
        assert backend.uploaded == [(b"PNGDATA", "ref.png")]
    finally:
        await o.stop()


async def test_budget_ceiling_forces_policy_off(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    await jobs.add(u["id"], "expensive")
    backend = FakeBackend()
    backend._cost = 999.0
    o = await _orch(app_settings, backend)
    try:
        await o.set_policy("auto")
        await o._tick()
        assert await o.policy() == "off"
        assert backend.up is False
    finally:
        await o.stop()


async def test_snapshot_for_a_user_hides_other_users(db, app_settings):
    a = await users.create("a@h3.local", "passphrase-1")
    b = await users.create("b@h3.local", "passphrase-2")
    await jobs.add(a["id"], "mine")
    await jobs.add(b["id"], "theirs")
    o = await _orch(app_settings)
    try:
        snap = await o.snapshot_for(a["id"])
        assert snap["counts"]["queued"] == 1
        assert snap["queue"]["total_queued"] == 2       # a number, not a list
        assert "prompt" not in str(snap)
    finally:
        await o.stop()
```

- [ ] **Step 2: Run and watch it fail**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL — `ImportError: cannot import name 'from_settings'` / `app.db` missing

- [ ] **Step 3: Rework `app/config.py`**

Delete `load()`, `save()`, the `yaml` import, `RunpodCfg.api_key` persistence to
file, `OutputCfg.folder` / `inbox`, and `ServerCfg`. Keep `WeightsCfg`,
`PodCfg`, `Preset`, `GenerationCfg`, `BudgetCfg` exactly as they are — the
generation presets and weight manifest are code, not deployment configuration.

Add to `Config`:

```python
    @classmethod
    def from_settings(cls, s: Any) -> "Config":
        """Build the runtime config from the environment.

        The presets, the weight manifest and the boot timeouts stay in code: they
        describe the model, not the deployment, and an operator who changes them
        by environment variable would be changing what H3 Studio renders rather
        than where it runs.
        """
        cfg = cls()
        cfg.runpod.api_key = s.runpod_api_key
        cfg.pod.policy = s.pod_policy
        cfg.budget.session_limit_usd = s.budget_session_limit_usd
        cfg.mock = s.mock
        return cfg

    def public(self) -> dict[str, Any]:
        """The subset safe to hand the browser - no api_key, no file paths."""
        return {
            "presets": {k: v.model_dump() for k, v in self.generation.presets.items()},
            "default_preset": self.generation.default_preset,
            "default_mode": self.generation.default_mode,
            "default_seconds": self.generation.default_seconds,
            "fps": self.generation.fps,
            "mock": self.mock,
        }
```

`public()` loses `output_folder`, `inbox_folder`, `session_limit_usd`,
`idle_shutdown_minutes` and the GPU list: those are now either meaningless to a
user or admin-only, and the browser had no business knowing the server's paths.

- [ ] **Step 4: Rewrite `app/orchestrator.py`**

Keep the whole policy machine, the ceilings, `MAX_ATTEMPTS`, `MAX_INFLIGHT`,
`MAX_POLL_ERRORS`, `TICK_SECONDS` and the module docstring's reasoning. Change:

1. The constructor takes its collaborators:

```python
    def __init__(self, cfg: Config, backend: Backend, sink: OutputSink,
                 storage: Any) -> None:
        self.cfg = cfg
        self.backend = backend
        self.sink = sink
        self.storage = storage
        self.leader = False
        self._lock_conn = None
        ...  # the existing private state is unchanged
```

2. `start()` takes the advisory lock and only loops if it wins:

```python
    async def start(self) -> None:
        """Claim leadership, then drain the queue.

        The lock is held on a dedicated connection for the life of the process.
        Two orchestrators would mean two rented pods billing at once and nothing
        would error - the bill would just double - so a process that cannot get
        the lock serves the web app and processes nothing.
        """
        self._lock_conn = await pool().getconn()
        self.leader = await try_advisory_lock(self._lock_conn,
                                              ORCHESTRATOR_LOCK_KEY)
        if not self.leader:
            log.warning("another orchestrator holds the queue lock; "
                        "this process will not start or stop any GPU")
            return
        orphaned = await jobs.requeue_stuck_running()
        if orphaned:
            log.info("requeued %d job(s) orphaned by a previous run", orphaned)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="orchestrator")
```

3. `stop()` releases the lock and returns the connection:

```python
    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.wait([self._task], timeout=20)
        if self.leader:
            # A pod outliving the process is the expensive failure mode.
            try:
                await self.backend.shutdown()
            except Exception:
                log.exception("shutdown during stop failed")
        await self.backend.aclose()
        if self._lock_conn is not None:
            await pool().putconn(self._lock_conn)
            self._lock_conn = None
        self.leader = False
```

4. Every `db.` call becomes its async `store` equivalent:
   `db.counts()` → `await jobs.counts_all()`;
   `db.claim_next_queued()` → `await jobs.claim_next_queued()`;
   `db.update_job(...)` → `await jobs.update(...)`;
   `db.get_job(id)` → `await jobs.get_any(id)`;
   `db.start_run/update_run` → `await runs.start/update`;
   `db.kv_get/kv_set` → `await kv.get/set`.

5. `policy` becomes async:

```python
    async def policy(self) -> str:
        return await kv.get("pod_policy", self.cfg.pod.policy) or "off"

    async def set_policy(self, value: str) -> None:
        if value not in {"auto", "keep-warm", "off"}:
            raise ValueError(f"bad policy {value!r}")
        await kv.set("pod_policy", value)
        self._notice = f"policy set to {value}"
```

6. `_dispatch` reads references from the object store:

```python
            try:
                for key in job.get("ref_images") or []:
                    data = await self.storage.get(key)
                    await self.backend.upload_image(data, Path(key).name)
                remote_id = await self.backend.submit(job)
```

7. `_collect` writes `output_key`:

```python
            try:
                key = await self.sink.put(job, res.video or b"", res.filename or "clip.mp4")
            except Exception as e:
                await self._fail_or_retry(job, f"saving output failed: {str(e)[:300]}")
                continue
            await jobs.update(job_id, status="done", finished_at=time.time(),
                              output_key=key, error=None)
```

8. `snapshot()` becomes async and gains a per-user variant:

```python
    async def snapshot(self) -> dict[str, Any]:
        """The shared, admin-facing view."""
        cost = getattr(self.backend, "cost_so_far", lambda: 0.0)()
        session_s = time.time() - self._session_started if self._session_started else 0.0
        return {
            "leader": self.leader,
            "policy": await self.policy(),
            "pod": {
                "state": self._pod_status.state,
                "detail": self._pod_status.detail,
                "pod_id": self._pod_status.pod_id,
                "uptime_s": round(self._pod_status.uptime_s, 1),
                "gpu": getattr(self.backend, "gpu_used", "") or "",
                "rate_per_hour": getattr(self.backend, "rate_per_hour", 0.0),
            },
            "counts": await jobs.counts_all(),
            "inflight": len(self._inflight),
            "session": {
                "seconds": round(session_s, 1),
                "cost_usd": round(cost, 4),
                "limit_usd": self.cfg.budget.session_limit_usd,
                "warn_usd": self.cfg.budget.warn_at_usd,
            },
            "backend": self.backend.name,
            "error": self._last_error,
            "notice": self._notice,
        }

    async def snapshot_for(self, user_id: str) -> dict[str, Any]:
        """What one user is allowed to see.

        Pod state and a queue depth, because a job waiting through a five-minute
        cold boot is otherwise indistinguishable from a broken one. No prompts, no
        costs, no other user's counts.
        """
        counts_all = await jobs.counts_all()
        return {
            "pod": {
                "state": self._pod_status.state,
                "detail": self._pod_status.detail,
                "uptime_s": round(self._pod_status.uptime_s, 1),
            },
            "counts": await jobs.counts_for(user_id),
            "queue": {
                "total_queued": counts_all["queued"],
                "total_running": counts_all["running"],
            },
            "backend": self.backend.name,
            "notice": self._notice,
        }
```

- [ ] **Step 5: Delete the old store**

```bash
git rm app/db.py
```

- [ ] **Step 6: Run the orchestrator tests**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Commit**

```bash
git add app/orchestrator.py app/config.py tests/test_orchestrator.py
git rm --cached app/db.py 2>/dev/null; git add -A app/db.py
git commit -m "Run one orchestrator, chosen by a Postgres advisory lock

The orchestrator owns a rented GPU. Two of them means two pods billing at once
and nothing errors - the bill just doubles. A process that cannot take the lock
serves the web app and touches no pod.

Collaborators are injected rather than built in the constructor, which is what
lets the tests drive the whole policy machine, the retry cap and the budget
ceiling without ffmpeg, a timer, or a RunPod account."
```

---
### Task 9: The app factory and the user-facing routes

**Files:**
- Rewrite: `app/main.py`
- Create: `app/routes/status.py`, `app/routes/jobs.py`, `app/routes/media.py`, `app/routes/archive.py`
- Create: `tests/test_jobs_routes.py`
- Modify: `tests/test_auth.py` (remove the `xfail` markers from Task 4)

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces:
  - `app.main.create_app() -> FastAPI` — reads settings itself, wires the lifespan
  - `app.main.main()` — the uvicorn entry point
  - `request.app.state.orch`, `.cfg`, `.storage` for routes
  - `app.routes.jobs.NewJobs`, `JobPatch`, `AgainAllBody` request models (fields unchanged from the old `main.py`, minus nothing)
  - `app.routes.jobs.split_prompts(text, mode)` moved verbatim from `main.py`

- [ ] **Step 1: Write the failing route tests**

Create `tests/test_jobs_routes.py`:

```python
from __future__ import annotations

from app.store import jobs, users


async def _signed_in(client, email="a@h3.local", role="user"):
    u = await users.create(email, "passphrase-1", role=role)
    r = await client.post("/api/auth/login",
                          json={"email": email, "password": "passphrase-1"})
    assert r.status_code == 200
    return u


async def test_creating_a_job_attaches_the_signed_in_user(client, db):
    u = await _signed_in(client)
    r = await client.post("/api/jobs", json={"prompts": "a red car"})
    assert r.status_code == 200 and r.json()["count"] == 1
    rows = await jobs.list_for(u["id"])
    assert rows[0]["prompt"] == "a red car"


async def test_the_feed_shows_only_my_jobs(client, db):
    other = await users.create("b@h3.local", "passphrase-2")
    await jobs.add(other["id"], "not mine")
    await _signed_in(client)
    await client.post("/api/jobs", json={"prompts": "mine"})
    listed = (await client.get("/api/jobs")).json()["jobs"]
    assert [j["prompt"] for j in listed] == ["mine"]


async def test_lines_split_makes_one_job_per_line(client, db):
    await _signed_in(client)
    r = await client.post("/api/jobs",
                          json={"prompts": "one\ntwo\nthree", "split": "lines"})
    assert r.json()["count"] == 3


async def test_a_paragraph_is_one_job_by_default(client, db):
    await _signed_in(client)
    r = await client.post("/api/jobs", json={"prompts": "one\ntwo\nthree"})
    assert r.json()["count"] == 1


async def test_takes_get_their_own_seeds(client, db):
    u = await _signed_in(client)
    await client.post("/api/jobs", json={"prompts": "p", "count": 3, "seed": 42})
    seeds = [j["seed"] for j in await jobs.list_for(u["id"])]
    assert seeds == [None, None, None]      # 3 takes sharing a seed would be identical


async def test_a_single_take_keeps_the_pinned_seed(client, db):
    u = await _signed_in(client)
    await client.post("/api/jobs", json={"prompts": "p", "count": 1, "seed": 42})
    assert (await jobs.list_for(u["id"]))[0]["seed"] == 42


async def test_unknown_preset_is_refused(client, db):
    await _signed_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "preset": "ultra"})
    assert r.status_code == 400


async def test_empty_prompt_is_refused(client, db):
    await _signed_in(client)
    assert (await client.post("/api/jobs", json={"prompts": "   "})).status_code == 400


async def test_editing_a_running_job_is_refused(client, db):
    u = await _signed_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="running")
    r = await client.patch(f"/api/jobs/{jid}", json={"prompt": "new"})
    assert r.status_code == 409


async def test_editing_a_finished_job_requeues_it(client, db):
    u = await _signed_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="done", output_key="k")
    r = await client.patch(f"/api/jobs/{jid}", json={"prompt": "revised"})
    assert r.status_code == 200 and r.json()["requeued"] is True
    row = await jobs.get_for(u["id"], jid)
    assert row["status"] == "queued" and row["prompt"] == "revised"


async def test_again_clones_rather_than_resetting(client, db):
    u = await _signed_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="done", output_key="k")
    r = await client.post(f"/api/jobs/{jid}/again")
    assert r.status_code == 200
    assert len(await jobs.list_for(u["id"])) == 2
    assert (await jobs.get_for(u["id"], jid))["status"] == "done"   # original kept


async def test_delete_removes_the_row(client, db):
    u = await _signed_in(client)
    jid = await jobs.add(u["id"], "p")
    assert (await client.delete(f"/api/jobs/{jid}")).status_code == 200
    assert await jobs.get_for(u["id"], jid) is None


async def test_status_reports_my_counts_and_a_queue_depth(client, db):
    other = await users.create("b@h3.local", "passphrase-2")
    await jobs.add(other["id"], "theirs")
    await _signed_in(client)
    await client.post("/api/jobs", json={"prompts": "mine"})
    body = (await client.get("/api/status")).json()
    assert body["counts"]["queued"] == 1
    assert body["queue"]["total_queued"] == 2
    assert "theirs" not in str(body)


async def test_archive_lists_only_my_finished_clips(client, db):
    u = await _signed_in(client)
    other = await users.create("b@h3.local", "passphrase-2")
    theirs = await jobs.add(other["id"], "theirs")
    await jobs.update(theirs, status="done", output_key="k", finished_at=1.0)
    mine = await jobs.add(u["id"], "mine")
    await jobs.update(mine, status="done", output_key="k2", finished_at=2.0)
    body = (await client.get("/api/archive")).json()
    assert [c["id"] for c in body["clips"]] == [mine]


async def test_estimate_needs_no_gpu(client, db):
    await _signed_in(client)
    r = await client.post("/api/estimate", json={"prompts": "a\nb", "split": "lines"})
    assert r.status_code == 200 and r.json()["clips"] == 2
```

- [ ] **Step 2: Run and watch it fail**

Run: `python -m pytest tests/test_jobs_routes.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_app'`

- [ ] **Step 3: Write `app/routes/status.py`**

```python
"""The polling endpoint, and liveness for the platform."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..auth import current_user

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def status(request: Request,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    orch = request.app.state.orch
    cfg = request.app.state.cfg
    snap = await orch.snapshot_for(user["id"])
    return {**snap, "config": cfg.public(),
            "user": {"email": user["email"], "role": user["role"]}}


# Deliberately outside the auth dependency: the platform's health check has no
# session, and an unauthenticated 401 would read as a dead container.
health_router = APIRouter(prefix="/api", tags=["health"])


@health_router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {"ok": True, "leader": getattr(request.app.state.orch, "leader", False)}
```

- [ ] **Step 4: Write `app/routes/jobs.py`**

```python
"""The queue, from the browser's side. Every read is scoped to the caller."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import current_user
from ..estimate import estimate_batch
from ..store import jobs as jobs_store

router = APIRouter(prefix="/api", tags=["jobs"],
                   dependencies=[Depends(current_user)])

MAX_TAKES = 10


def split_prompts(text: str, mode: str | None) -> list[str]:
    """Turn the prompt box into one or more prompts.

    Single is the default because the two mistakes are not symmetrical: splitting
    a paragraph that happened to contain a line break silently produces extra
    clips from half-sentences and bills for them, while failing to split merely
    produces one job you can see and fix.
    """
    if (mode or "single") == "lines":
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = text.strip()
    return [text] if text else []


class NewJobs(BaseModel):
    prompts: str = ""
    split: str | None = None
    seconds: int | None = None
    preset: str | None = None
    seed: int | None = None
    mode: str | None = None
    ref_images: list[str] = Field(default_factory=list)
    count: int = 1


class JobPatch(BaseModel):
    """Every field optional - the editor sends only what changed."""
    prompt: str | None = None
    seconds: int | None = None
    preset: str | None = None
    mode: str | None = None
    ref_images: list[str] | None = None


class AgainAllBody(BaseModel):
    status: str = "done"


def _owned_keys(user_id: str, keys: list[str]) -> list[str]:
    """Keep only object keys inside this user's own prefix.

    The list arrives from the browser, so a key naming someone else's upload has
    to be dropped here rather than trusted into a job row.
    """
    prefix = f"uploads/{user_id}/"
    return [k for k in keys if k.startswith(prefix) and ".." not in k]


@router.get("/jobs")
async def list_jobs(user: dict = Depends(current_user)) -> dict[str, Any]:
    return {"jobs": await jobs_store.list_for(user["id"])}


@router.post("/jobs")
async def add_jobs(body: NewJobs, request: Request,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    cfg = request.app.state.cfg
    prompts = split_prompts(body.prompts, body.split)
    if not prompts:
        raise HTTPException(400, "no prompts given")
    takes = max(1, min(MAX_TAKES, body.count))
    preset = body.preset or cfg.generation.default_preset
    if preset not in cfg.generation.presets:
        raise HTTPException(400, f"unknown preset {preset!r}")
    mode = body.mode or cfg.generation.default_mode
    if mode not in {"t2v", "i2v", "r2v"}:
        raise HTTPException(400, f"unknown mode {mode!r}")
    refs = _owned_keys(user["id"], body.ref_images)
    created: list[str] = []
    for prompt in prompts:
        for _ in range(takes):
            # Only pin the seed for a single take; several takes sharing one seed
            # would come out identical, which is never what "3 takes" means.
            seed = body.seed if (body.seed is not None and takes == 1) else None
            created.append(await jobs_store.add(
                user["id"], prompt,
                seconds=body.seconds or cfg.generation.default_seconds,
                ref_images=refs, seed=seed, mode=mode, preset=preset))
    return {"created": created, "count": len(created)}


async def _mine_or_404(user: dict, job_id: str) -> dict[str, Any]:
    job = await jobs_store.get_for(user["id"], job_id)
    if job is None:
        # 404 rather than 403: a 403 would confirm that somebody else's job with
        # this id exists.
        raise HTTPException(404, "no such job")
    return job


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    return await _mine_or_404(user, job_id)


@router.patch("/jobs/{job_id}")
async def patch_job(job_id: str, body: JobPatch, request: Request,
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """Edit a job.

    A running job is refused rather than silently edited: it is already on the
    GPU, so a change could not take effect. Editing a finished one puts it back in
    the queue, because the only reason to edit a finished job is to run it again.
    """
    cfg = request.app.state.cfg
    job = await _mine_or_404(user, job_id)
    if job["status"] == "running":
        raise HTTPException(409, "that job is generating right now - cancel it first")

    fields: dict[str, Any] = {}
    if body.prompt is not None:
        if not body.prompt.strip():
            raise HTTPException(400, "prompt cannot be empty")
        fields["prompt"] = body.prompt.strip()[:2000]
    if body.seconds is not None:
        fields["seconds"] = max(4, min(15, body.seconds))
    if body.preset is not None:
        if body.preset not in cfg.generation.presets:
            raise HTTPException(400, f"unknown preset {body.preset!r}")
        fields["preset"] = body.preset
    if body.mode is not None:
        if body.mode not in {"t2v", "i2v", "r2v"}:
            raise HTTPException(400, f"unknown mode {body.mode!r}")
        fields["mode"] = body.mode
    if body.ref_images is not None:
        fields["ref_images"] = _owned_keys(user["id"], body.ref_images)

    requeued = job["status"] in {"done", "failed", "cancelled"}
    if requeued:
        fields.update(status="queued", attempts=0, error=None, remote_id=None,
                      output_key=None, finished_at=None)
    await jobs_store.update(job_id, **fields)
    return {"ok": True, "requeued": requeued,
            "job": await jobs_store.get_for(user["id"], job_id)}


@router.post("/jobs/{job_id}/retry")
async def retry(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    await _mine_or_404(user, job_id)
    await jobs_store.update(job_id, status="queued", attempts=0, error=None,
                            remote_id=None, output_key=None)
    return {"ok": True}


@router.post("/jobs/{job_id}/again")
async def run_again(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """Queue a fresh copy of a finished job.

    A clone rather than a reset, so the earlier take and its file stay on record -
    re-rolling a prompt is how this is used, and comparing takes is the point. The
    seed is deliberately not copied: reusing it would reproduce the same clip.
    """
    job = await _mine_or_404(user, job_id)
    new_id = await jobs_store.add(
        user["id"], job["prompt"], seconds=job["seconds"],
        ref_images=job["ref_images"], mode=job["mode"], preset=job["preset"])
    return {"ok": True, "job_id": new_id}


@router.post("/jobs/again-all")
async def run_all_again(body: AgainAllBody,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    wanted = body.status or "done"
    if wanted not in {"done", "failed", "cancelled"}:
        raise HTTPException(400, "status must be done, failed or cancelled")
    created = 0
    for job in await jobs_store.list_for(user["id"]):
        if job["status"] != wanted:
            continue
        await jobs_store.add(user["id"], job["prompt"], seconds=job["seconds"],
                             ref_images=job["ref_images"], mode=job["mode"],
                             preset=job["preset"])
        created += 1
    return {"queued": created}


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    await _mine_or_404(user, job_id)
    await jobs_store.update(job_id, status="cancelled")
    return {"ok": True}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """Remove one entry from the list. The stored clip is left alone - the queue is
    a work list, not the archive, and deleting a row should never destroy something
    the user waited and paid for."""
    job = await _mine_or_404(user, job_id)
    if job["status"] == "running":
        raise HTTPException(409, "that job is generating right now - cancel it first")
    await jobs_store.delete_for(user["id"], job_id)
    return {"ok": True}


@router.post("/jobs/clear-finished")
async def clear_finished(user: dict = Depends(current_user)) -> dict[str, Any]:
    return {"removed": await jobs_store.clear_finished_for(user["id"])}


@router.post("/estimate")
async def estimate(body: NewJobs, request: Request) -> dict[str, Any]:
    cfg = request.app.state.cfg
    prompts = split_prompts(body.prompts, body.split)
    clips = max(1, len(prompts)) * max(1, min(MAX_TAKES, body.count))
    preset = cfg.generation.preset(body.preset)
    seconds = body.seconds or cfg.generation.default_seconds
    gpu = cfg.runpod.gpu_preference[0] if cfg.runpod.gpu_preference else ""
    return {"clips": clips,
            **estimate_batch(gpu, clips, preset, seconds, cfg)}
```

- [ ] **Step 5: Write `app/routes/media.py`**

```python
"""Bytes in and bytes out: reference uploads, video playback.

Everything here is decided by the database row, never by the shape of a key the
browser sent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import (APIRouter, Depends, File, HTTPException, Request, Response,
                     UploadFile)
from fastapi.responses import StreamingResponse

from .. import storage as storage_mod
from ..auth import current_user
from ..store import jobs as jobs_store

router = APIRouter(prefix="/api", tags=["media"],
                   dependencies=[Depends(current_user)])

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...),
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    suffix = Path(file.filename or "ref.png").suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise HTTPException(400, f"{suffix or 'that file'} is not an image")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "reference images are limited to 32 MB")
    key = storage_mod.upload_key(user["id"], suffix)
    await request.app.state.storage.put(key, data, file.content_type or "image/png")
    return {"key": key, "name": Path(key).name}


@router.get("/image/{key:path}")
async def get_image(key: str, request: Request,
                    user: dict = Depends(current_user)):
    """Serve one of this user's own uploads.

    The prefix check is the authorization: an upload is not attached to a job row
    until it is used, so there is nothing else to check it against.
    """
    if not key.startswith(f"uploads/{user['id']}/") or ".." in key:
        raise HTTPException(404, "no such image")
    try:
        data = await request.app.state.storage.get(key)
    except storage_mod.ObjectMissing:
        raise HTTPException(404, "no such image")
    return Response(data, media_type="image/png")


@router.get("/video/{job_id}")
async def video(job_id: str, request: Request,
                user: dict = Depends(current_user)):
    job = await jobs_store.get_for(user["id"], job_id)
    if job is None or not job.get("output_key"):
        raise HTTPException(404, "no output for that job")
    store = request.app.state.storage
    byte_range = request.headers.get("range")
    try:
        chunks, size, content_range = await store.stream(job["output_key"], byte_range)
    except storage_mod.ObjectMissing:
        raise HTTPException(404, "that clip is no longer stored")
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(size)}
    status = 200
    if content_range:
        headers["Content-Range"] = content_range
        status = 206
    return StreamingResponse(chunks, status_code=status, media_type="video/mp4",
                             headers=headers)
```

- [ ] **Step 6: Write `app/routes/archive.py`**

```python
"""The user's own finished clips."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ..auth import current_user
from ..store import jobs as jobs_store

router = APIRouter(prefix="/api", tags=["archive"],
                   dependencies=[Depends(current_user)])


@router.get("/archive")
async def archive(cursor: str | None = Query(default=None),
                  limit: int = Query(default=24, ge=1, le=100),
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    clips, next_cursor = await jobs_store.archive_page(user["id"], cursor, limit)
    return {
        "clips": [{
            "id": c["id"],
            "prompt": c["prompt"],
            "seconds": c["seconds"],
            "preset": c["preset"],
            "mode": c["mode"],
            "finished_at": c["finished_at"],
            "bytes": c["output_bytes"],
            "video_url": f"/api/video/{c['id']}",
        } for c in clips],
        "next_cursor": next_cursor,
    }
```

The archive returns a URL rather than a key: the browser never needs to know
where a clip is stored, and a key in the payload is a key someone will try to
fetch directly.

- [ ] **Step 7: Rewrite `app/main.py`**

```python
"""The ASGI app: wiring, lifespan, and the pages.

The browser is a dumb client of this API - no logic, no secrets. Every /api route
is guarded by an explicit dependency rather than by a middleware that matches
paths, so a route added later is not accidentally public.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config as config_mod
from . import storage as storage_mod
from .orchestrator import Orchestrator
from .routes import admin, archive, auth as auth_routes, jobs as job_routes, media, status
from .settings import Settings, get_settings
from .sinks import make_sink
from .store import migrate, close_pool, connection, open_pool, users

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

log = logging.getLogger("h3studio")


def make_backend(cfg: config_mod.Config):
    if cfg.mock:
        from .backends.mock import MockBackend
        return MockBackend(cfg)
    if not cfg.runpod.api_key:
        raise SystemExit(
            "No RunPod API key. Set RUNPOD_API_KEY, or set an admin key from the "
            "admin page once the app is up, or set MOCK=true to run the whole "
            "pipeline without renting anything.")
    from .backends.runpod_pod import RunpodBackend
    return RunpodBackend(cfg)


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    cfg = config_mod.Config.from_settings(s)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await open_pool(s.database_url)
        async with connection() as conn:
            applied = await migrate(conn)
        if applied:
            log.info("applied migrations: %s", ", ".join(applied))
        await users.ensure_bootstrap_admin(s.admin_email, s.admin_password)

        store = storage_mod.get_storage()
        if s.output_sink == "s3":
            await store.ensure_bucket()
        app.state.storage = store

        orch = Orchestrator(cfg, make_backend(cfg), make_sink(s), store)
        app.state.orch = orch
        await orch.start()
        try:
            yield
        finally:
            # Runs on shutdown of any kind. A pod outliving the process is the one
            # failure mode here that costs real money.
            await orch.stop()
            await close_pool()

    app = FastAPI(title="H3 Studio", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.cfg = cfg
    app.state.settings = s

    app.include_router(status.health_router)
    app.include_router(auth_routes.router)
    app.include_router(status.router)
    app.include_router(job_routes.router)
    app.include_router(media.router)
    app.include_router(archive.router)
    app.include_router(admin.router)

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

    def _page(name: str):
        async def render():
            page = WEB / name
            if not page.exists():
                return JSONResponse({"error": f"web/{name} missing"}, status_code=500)
            html = page.read_text(encoding="utf-8")
            # Fingerprint the asset URLs. Without it the browser keeps a cached
            # app.js after a deploy, so the page renders new markup while running
            # old code - which looks like a bug in whatever you just changed.
            for asset in ("app.js", "style.css", "auth.js", "archive.js", "admin.js"):
                path = WEB / asset
                if path.exists():
                    stamp = f"{int(path.stat().st_mtime)}-{path.stat().st_size}"
                    html = html.replace(f"/static/{asset}", f"/static/{asset}?v={stamp}")
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
        return render

    # The pages are served without a session check; each one's first API call
    # returns 401 and its script redirects to /login. Gating the HTML too would
    # only mean maintaining the same rule twice.
    app.get("/")(_page("index.html"))
    app.get("/login")(_page("login.html"))
    app.get("/archive")(_page("archive.html"))
    app.get("/admin")(_page("admin.html"))

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    s = get_settings()
    banner = "MOCK (no GPU, no cost)" if s.mock else "RunPod"
    log.info("H3 Studio on %s:%d [%s]", s.host, s.port, banner)
    # workers=1 is not a tuning choice: the orchestrator owns a rented GPU, and a
    # second worker would start a second pod. The advisory lock backs this up.
    uvicorn.run(create_app(s), host=s.host, port=s.port, workers=1,
                log_level="info", proxy_headers=True,
                forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
```

`proxy_headers` and `forwarded_allow_ips` matter behind Dokploy's reverse proxy:
without them `request.client.host` is the proxy for every request, and the login
rate limiter would throttle all users together.

- [ ] **Step 8: Remove the xfail markers from Task 4**

Delete the `@pytest.mark.xfail` decorators added to the route tests in
`tests/test_auth.py`.

- [ ] **Step 9: Run the route and auth tests**

Run: `python -m pytest tests/test_jobs_routes.py tests/test_auth.py -v`
Expected: PASS (15 + 10 tests). Task 10 adds `app/routes/admin.py`; until then
stub it as an empty `APIRouter` so the import in `main.py` resolves.

- [ ] **Step 10: Commit**

```bash
git add app/main.py app/routes tests/test_jobs_routes.py tests/test_auth.py
git commit -m "Split main.py into guarded routers, one per concern

Each router carries the auth dependency itself rather than relying on a
middleware that matches on path prefixes, so a route added later cannot end up
public by omission.

Reference keys arriving from the browser are filtered to the caller's own
uploads/ prefix before they reach a job row, and a job belonging to someone else
answers 404 - a 403 would confirm it exists."
```

---
### Task 10: Admin routes

**Files:**
- Create (replacing the Task 9 stub): `app/routes/admin.py`
- Create: `tests/test_admin.py`

**Interfaces:**
- Consumes: `app.auth.require_admin`, `app.store.users`, `app.store.jobs`, `app.store.runs`, `app.store.kv`.
- Produces: `router` with `GET/POST /api/admin/users`, `PATCH /api/admin/users/{id}`, `GET /api/admin/jobs`, `GET /api/admin/status`, `POST /api/admin/policy`, `POST /api/admin/runpod-key`, `POST /api/admin/budget`, `GET /api/admin/runs`.

- [ ] **Step 1: Write the failing admin tests**

Create `tests/test_admin.py`:

```python
from __future__ import annotations

import httpx
import pytest

from app.store import jobs, kv, users


async def _sign_in(client, email, role):
    await users.create(email, "passphrase-1", role=role)
    r = await client.post("/api/auth/login",
                          json={"email": email, "password": "passphrase-1"})
    assert r.status_code == 200


ADMIN_ROUTES = [
    ("GET", "/api/admin/users", None),
    ("POST", "/api/admin/users", {"email": "x@h3.local", "password": "passphrase-9"}),
    ("GET", "/api/admin/jobs", None),
    ("GET", "/api/admin/status", None),
    ("POST", "/api/admin/policy", {"policy": "off"}),
    ("POST", "/api/admin/budget", {"session_limit_usd": 5.0}),
    ("GET", "/api/admin/runs", None),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
async def test_normal_users_are_refused_everywhere(client, db, method, path, body):
    await _sign_in(client, "u@h3.local", "user")
    r = await client.request(method, path, json=body)
    assert r.status_code == 403, path


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
async def test_anonymous_is_refused_everywhere(client, db, method, path, body):
    r = await client.request(method, path, json=body)
    assert r.status_code == 401, path


async def test_admin_creates_a_user_who_can_sign_in(client, db):
    await _sign_in(client, "boss@h3.local", "admin")
    r = await client.post("/api/admin/users",
                          json={"email": "new@h3.local", "password": "passphrase-2"})
    assert r.status_code == 200
    assert (await users.by_email("new@h3.local")) is not None


async def test_creating_a_duplicate_is_a_clean_400(client, db):
    await _sign_in(client, "boss@h3.local", "admin")
    body = {"email": "dup@h3.local", "password": "passphrase-2"}
    assert (await client.post("/api/admin/users", json=body)).status_code == 200
    r = await client.post("/api/admin/users", json=body)
    assert r.status_code == 400 and "already" in r.json()["detail"]


async def test_admin_disables_a_user(client, db):
    await _sign_in(client, "boss@h3.local", "admin")
    u = await users.create("victim@h3.local", "passphrase-3")
    r = await client.patch(f"/api/admin/users/{u['id']}", json={"is_active": False})
    assert r.status_code == 200
    assert (await users.by_id(u["id"]))["is_active"] is False


async def test_admin_cannot_disable_themselves(client, db):
    await _sign_in(client, "boss@h3.local", "admin")
    me = await users.by_email("boss@h3.local")
    r = await client.patch(f"/api/admin/users/{me['id']}", json={"is_active": False})
    assert r.status_code == 400


async def test_admin_sees_every_job(client, db):
    await _sign_in(client, "boss@h3.local", "admin")
    other = await users.create("b@h3.local", "passphrase-2")
    await jobs.add(other["id"], "theirs")
    body = (await client.get("/api/admin/jobs")).json()
    assert [j["prompt"] for j in body["jobs"]] == ["theirs"]
    assert body["jobs"][0]["user_email"] == "b@h3.local"


async def test_setting_policy_persists(client, db):
    await _sign_in(client, "boss@h3.local", "admin")
    assert (await client.post("/api/admin/policy",
                              json={"policy": "keep-warm"})).status_code == 200
    assert await kv.get("pod_policy") == "keep-warm"


async def test_bad_policy_is_refused(client, db):
    await _sign_in(client, "boss@h3.local", "admin")
    r = await client.post("/api/admin/policy", json={"policy": "sometimes"})
    assert r.status_code == 400


async def test_runpod_key_is_verified_before_it_is_saved(client, db, monkeypatch):
    await _sign_in(client, "boss@h3.local", "admin")

    class Reject:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k):
            return httpx.Response(401, request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr("app.routes.admin.httpx.AsyncClient", lambda **k: Reject())
    r = await client.post("/api/admin/runpod-key", json={"key": "rp-bogus"})
    assert r.status_code == 400
    assert await kv.get("runpod_api_key") is None


async def test_a_verified_runpod_key_is_stored_and_never_echoed(client, db, monkeypatch):
    await _sign_in(client, "boss@h3.local", "admin")

    class Accept:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k):
            return httpx.Response(200, request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr("app.routes.admin.httpx.AsyncClient", lambda **k: Accept())
    r = await client.post("/api/admin/runpod-key", json={"key": "rp-secret-value"})
    assert r.status_code == 200
    assert r.json()["hint"] == "alue"
    assert "rp-secret-value" not in r.text
    assert await kv.get("runpod_api_key") == "rp-secret-value"
```

- [ ] **Step 2: Run and watch it fail**

Run: `python -m pytest tests/test_admin.py -v`
Expected: FAIL — 404 on every admin route (the Task 9 stub router is empty)

- [ ] **Step 3: Write `app/routes/admin.py`**

```python
"""Everything one person controls on everyone's behalf.

Separate routes rather than a role branch inside the normal ones: there is no
path here that a non-admin can reach at all, so there is no branch to get wrong.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import require_admin
from ..store import jobs as jobs_store
from ..store import kv, runs, users

router = APIRouter(prefix="/api/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])


class NewUser(BaseModel):
    email: str
    password: str
    role: str = "user"


class UserPatch(BaseModel):
    is_active: bool | None = None
    password: str | None = None
    role: str | None = None


class PolicyBody(BaseModel):
    policy: str


class KeyBody(BaseModel):
    key: str


class BudgetBody(BaseModel):
    session_limit_usd: float


@router.get("/users")
async def list_users() -> dict[str, Any]:
    return {"users": await users.list_all()}


@router.post("/users")
async def create_user(body: NewUser) -> dict[str, Any]:
    try:
        return await users.create(body.email, body.password, role=body.role)
    except users.EmailTaken as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, body: UserPatch,
                     admin: dict = Depends(require_admin)) -> dict[str, Any]:
    target = await users.by_id(user_id)
    if target is None:
        raise HTTPException(404, "no such user")
    if body.is_active is False and str(target["id"]) == str(admin["id"]):
        # Locking the last admin out of their own instance needs a database
        # console to undo, so refuse the obvious version of it.
        raise HTTPException(400, "you cannot disable your own account")
    if body.is_active is not None:
        await users.set_active(user_id, body.is_active)
    if body.password is not None:
        try:
            await users.set_password(user_id, body.password)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if body.role is not None:
        try:
            await users.set_role(user_id, body.role)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return await users.by_id(user_id)


@router.get("/jobs")
async def all_jobs() -> dict[str, Any]:
    return {"jobs": await jobs_store.list_all()}


@router.get("/status")
async def admin_status(request: Request) -> dict[str, Any]:
    return await request.app.state.orch.snapshot()


@router.post("/policy")
async def set_policy(body: PolicyBody, request: Request) -> dict[str, Any]:
    try:
        await request.app.state.orch.set_policy(body.policy)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"policy": await request.app.state.orch.policy()}


@router.post("/budget")
async def set_budget(body: BudgetBody, request: Request) -> dict[str, Any]:
    if not 0 < body.session_limit_usd <= 1000:
        raise HTTPException(400, "the session limit must be between 0 and 1000 USD")
    await kv.set("budget_session_limit_usd", str(body.session_limit_usd))
    request.app.state.cfg.budget.session_limit_usd = body.session_limit_usd
    return {"session_limit_usd": body.session_limit_usd}


@router.post("/runpod-key")
async def set_runpod_key(body: KeyBody, request: Request) -> dict[str, Any]:
    """Save the RunPod key, after RunPod agrees it is real.

    A typo saved silently would only surface later as a failed pod start, by which
    point the operator has no idea which of several things went wrong.

    Stored in the database, not a file: a container filesystem does not survive a
    redeploy. Never returned - only its last four characters.
    """
    key = body.key.strip()
    if not key:
        raise HTTPException(400, "no key given")
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get("https://rest.runpod.io/v1/pods",
                            headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"could not reach RunPod to verify the key: {e}")
    if r.status_code == 401:
        raise HTTPException(400, "RunPod rejected that key. Check you copied all of "
                                 "it, and that its permission level is All.")
    if r.status_code >= 400:
        raise HTTPException(400, f"RunPod returned {r.status_code} for that key")

    await kv.set("runpod_api_key", key)
    request.app.state.cfg.runpod.api_key = key
    return {"ok": True, "hint": key[-4:], "restart_required": True,
            "note": "Key verified and saved. Redeploy or restart to use it - the "
                    "backend is chosen when the process starts."}


@router.get("/runs")
async def recent_runs() -> dict[str, Any]:
    rows = await runs.recent()
    return {"runs": rows,
            "total_cost_usd": round(sum(r["cost_estimate"] for r in rows), 4)}
```

- [ ] **Step 4: Read the stored key and budget at startup**

In `create_app`'s lifespan, after migrations and before `make_backend`:

```python
        # kv wins over the environment: it is what an admin set from the UI, and
        # it is the copy that survives a redeploy.
        if stored_key := await kv.get("runpod_api_key"):
            cfg.runpod.api_key = stored_key
        if stored_budget := await kv.get("budget_session_limit_usd"):
            cfg.budget.session_limit_usd = float(stored_budget)
```

Add `kv` to the `from .store import …` line in `main.py`.

- [ ] **Step 5: Run the admin tests**

Run: `python -m pytest tests/test_admin.py -v`
Expected: PASS (24 tests, counting the parametrized pairs)

- [ ] **Step 6: Commit**

```bash
git add app/routes/admin.py app/main.py tests/test_admin.py
git commit -m "Add the admin panel's API

The RunPod key and the budget ceiling live in the database rather than in a
file: a container filesystem does not survive a redeploy, and 'the GPU policy
reset itself when we shipped a CSS fix' is an expensive kind of surprise.

The key is verified against RunPod before it is stored, and never returned -
only its last four characters, which is enough to recognise which key is loaded."
```

---

### Task 11: The isolation test suite

**Files:**
- Create: `tests/test_isolation.py`

This task adds no production code. It exists because tenant isolation is the one
property whose failure is silent, and a route added six months from now must fail
this suite rather than quietly leak.

**Interfaces:**
- Consumes: everything.
- Produces: nothing importable.

- [ ] **Step 1: Write the table-driven isolation test**

Create `tests/test_isolation.py`:

```python
"""Every route that takes a job id, proven blind to other people's jobs.

Written as one table rather than a test per route so that adding a route without
adding it here is visible: the table is the checklist.
"""
from __future__ import annotations

import pytest

from app.store import jobs, users

# (method, path template, json body)
JOB_ROUTES = [
    ("GET", "/api/jobs/{jid}", None),
    ("PATCH", "/api/jobs/{jid}", {"prompt": "stolen"}),
    ("DELETE", "/api/jobs/{jid}", None),
    ("POST", "/api/jobs/{jid}/retry", None),
    ("POST", "/api/jobs/{jid}/again", None),
    ("POST", "/api/jobs/{jid}/cancel", None),
    ("GET", "/api/video/{jid}", None),
]


async def _victim_job() -> tuple[dict, str]:
    victim = await users.create("victim@h3.local", "passphrase-1")
    jid = await jobs.add(victim["id"], "the victim's secret prompt")
    await jobs.update(jid, status="done", output_key=f"videos/{victim['id']}/{jid}.mp4",
                      finished_at=1.0)
    return victim, jid


async def _sign_in_attacker(client) -> dict:
    a = await users.create("attacker@h3.local", "passphrase-2")
    await client.post("/api/auth/login",
                      json={"email": "attacker@h3.local", "password": "passphrase-2"})
    return a


@pytest.mark.parametrize("method,path,body", JOB_ROUTES)
async def test_another_users_job_is_404_not_403(client, db, method, path, body):
    _, jid = await _victim_job()
    await _sign_in_attacker(client)
    r = await client.request(method, path.format(jid=jid), json=body)
    # 403 would confirm the job exists.
    assert r.status_code == 404, f"{method} {path} returned {r.status_code}"
    assert "secret prompt" not in r.text


@pytest.mark.parametrize("method,path,body", JOB_ROUTES)
async def test_anonymous_gets_401_everywhere(client, db, method, path, body):
    _, jid = await _victim_job()
    r = await client.request(method, path.format(jid=jid), json=body)
    assert r.status_code == 401, f"{method} {path} returned {r.status_code}"


async def test_a_failed_edit_does_not_change_the_victims_job(client, db):
    victim, jid = await _victim_job()
    await _sign_in_attacker(client)
    await client.patch(f"/api/jobs/{jid}", json={"prompt": "stolen"})
    assert (await jobs.get_for(victim["id"], jid))["prompt"] == \
        "the victim's secret prompt"


async def test_a_failed_delete_does_not_remove_the_victims_job(client, db):
    victim, jid = await _victim_job()
    await _sign_in_attacker(client)
    await client.delete(f"/api/jobs/{jid}")
    assert await jobs.get_for(victim["id"], jid) is not None


async def test_reference_keys_from_another_prefix_are_dropped(client, db):
    victim = await users.create("victim@h3.local", "passphrase-1")
    attacker = await _sign_in_attacker(client)
    r = await client.post("/api/jobs", json={
        "prompts": "borrowed",
        "ref_images": [f"uploads/{victim['id']}/theirs.png",
                       f"uploads/{attacker['id']}/mine.png"],
    })
    assert r.status_code == 200
    row = (await jobs.list_for(attacker["id"]))[0]
    assert row["ref_images"] == [f"uploads/{attacker['id']}/mine.png"]


async def test_traversal_in_an_image_key_is_refused(client, db):
    attacker = await _sign_in_attacker(client)
    r = await client.get(f"/api/image/uploads/{attacker['id']}/../../videos/x.mp4")
    assert r.status_code == 404


async def test_another_users_image_prefix_is_refused(client, db):
    victim = await users.create("victim@h3.local", "passphrase-1")
    await _sign_in_attacker(client)
    r = await client.get(f"/api/image/uploads/{victim['id']}/theirs.png")
    assert r.status_code == 404


async def test_again_all_only_touches_my_own_jobs(client, db):
    victim, jid = await _victim_job()
    attacker = await _sign_in_attacker(client)
    r = await client.post("/api/jobs/again-all", json={"status": "done"})
    assert r.json()["queued"] == 0
    assert len(await jobs.list_for(attacker["id"])) == 0
    assert len(await jobs.list_for(victim["id"])) == 1


async def test_clear_finished_only_touches_my_own_jobs(client, db):
    victim, jid = await _victim_job()
    await _sign_in_attacker(client)
    r = await client.post("/api/jobs/clear-finished")
    assert r.json()["removed"] == 0
    assert await jobs.get_for(victim["id"], jid) is not None


async def test_status_never_leaks_another_users_prompt(client, db):
    await _victim_job()
    await _sign_in_attacker(client)
    body = (await client.get("/api/status")).text
    assert "secret prompt" not in body


async def test_every_api_route_requires_a_session(client, db):
    """A route that forgot its dependency shows up here rather than in production."""
    from app.main import create_app
    from app.settings import get_settings
    app = create_app(get_settings())
    public = {"/api/health", "/api/auth/login", "/api/auth/logout"}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api") or path in public:
            continue
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            url = path.replace("{job_id}", "x").replace("{user_id}", "x") \
                      .replace("{key:path}", "x")
            r = await client.request(method, url, json={})
            assert r.status_code in (401, 403), f"{method} {path} -> {r.status_code}"
```

- [ ] **Step 2: Run the suite**

Run: `python -m pytest tests/test_isolation.py -v`
Expected: PASS. Any failure is a real leak — fix the route, not the test.

- [ ] **Step 3: Run everything**

Run: `python -m pytest -v`
Expected: PASS across all files.

- [ ] **Step 4: Commit**

```bash
git add tests/test_isolation.py
git commit -m "Prove tenant isolation route by route

Isolation is the property whose failure is silent: nothing errors, one user
simply sees another's work. The route table doubles as the checklist, and the
last test walks the app's own route list so a handler that forgets its auth
dependency fails here rather than in production."
```

---
### Task 12: Batch uploads without a watched folder

**Files:**
- Create: `app/batch.py` (the parser, lifted out of `inbox.py`)
- Delete: `app/inbox.py`
- Modify: `app/routes/media.py` (add `POST /api/inbox/upload`)
- Create: `tests/test_batch.py`

**Interfaces:**
- Consumes: `app.storage`.
- Produces (`app.batch`):
  - `IMAGE_SUFFIXES`, `BATCH_SUFFIXES`, `ARCHIVE_SUFFIXES` — unchanged sets
  - `parse(text: str, suffix: str) -> list[dict]` — raises `ValueError` on malformed input; each dict has `prompt`, `seconds`, `takes`, `mode`, `preset`, `ref_names`
  - `async unpack_zip(data: bytes, user_id: str, storage) -> tuple[list[dict], dict[str, str]]` — returns parsed jobs and a map of original image filename to stored S3 key
- The old `Inbox` class, its `scan`, `_seen`, `_waiting`, `_retire` and the 45-second replay buffer in `main.py` all disappear: they existed to make a filesystem poll idempotent, and there is no poll any more.

- [ ] **Step 1: Write the failing batch tests**

Create `tests/test_batch.py`:

```python
from __future__ import annotations

import io
import json
import zipfile

import pytest

from app import batch


def test_plain_text_is_one_prompt_per_line():
    jobs = batch.parse("first\n\n# a comment\nsecond\n", ".txt")
    assert [j["prompt"] for j in jobs] == ["first", "second"]


def test_text_jobs_get_defaults():
    j = batch.parse("only\n", ".txt")[0]
    assert j["takes"] == 1 and j["seconds"] == 10 and j["ref_names"] == []


def test_json_object_shape():
    text = json.dumps({"defaults": {"seconds": 6, "preset": "turbo"},
                       "jobs": [{"prompt": "a", "takes": 2},
                                {"prompt": "b", "seconds": 12}]})
    a, b = batch.parse(text, ".json")
    assert (a["prompt"], a["takes"], a["seconds"], a["preset"]) == ("a", 2, 6, "turbo")
    assert b["seconds"] == 12


def test_json_bare_list_of_strings():
    assert [j["prompt"] for j in batch.parse('["a","b"]', ".json")] == ["a", "b"]


def test_json_bare_list_of_objects():
    assert batch.parse('[{"prompt":"a"}]', ".json")[0]["prompt"] == "a"


def test_malformed_json_names_the_problem():
    with pytest.raises(ValueError) as e:
        batch.parse("{not json", ".json")
    assert "JSON" in str(e.value)


def test_out_of_range_numbers_are_clamped_not_rejected():
    j = batch.parse(json.dumps([{"prompt": "a", "seconds": 900, "takes": 99}]),
                    ".json")[0]
    assert j["seconds"] == 15 and j["takes"] == 10


def test_unknown_mode_falls_back_to_the_default():
    j = batch.parse(json.dumps([{"prompt": "a", "mode": "x2v"}]), ".json")[0]
    assert j["mode"] == "i2v"


async def test_unpack_zip_stores_images_and_maps_them(s3):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("prompts.txt", "a clip\n")
        z.writestr("ref.png", b"\x89PNG-not-really")
    jobs, images = await batch.unpack_zip(buf.getvalue(), "u1", s3)
    assert [j["prompt"] for j in jobs] == ["a clip"]
    assert "ref.png" in images
    assert images["ref.png"].startswith("uploads/u1/")
    assert await s3.get(images["ref.png"]) == b"\x89PNG-not-really"


async def test_zip_entries_cannot_escape_the_prefix(s3):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../etc/passwd.png", b"x")
        z.writestr("nested/dir/ok.png", b"y")
    _, images = await batch.unpack_zip(buf.getvalue(), "u1", s3)
    assert all(k.startswith("uploads/u1/") for k in images.values())
    assert set(images) == {"passwd.png", "ok.png"}


async def test_json_ref_names_resolve_to_stored_keys(s3):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("b.json", json.dumps([{"prompt": "a", "image": "ref.png"}]))
        z.writestr("ref.png", b"img")
    jobs, images = await batch.unpack_zip(buf.getvalue(), "u1", s3)
    assert jobs[0]["ref_names"] == ["ref.png"]
    assert images["ref.png"] in (v for v in images.values())
```

- [ ] **Step 2: Run and watch it fail**

Run: `python -m pytest tests/test_batch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.batch'`

- [ ] **Step 3: Write `app/batch.py`**

Port the parsing from `app/inbox.py:285-374` verbatim in behaviour — `_parse`,
`_parse_json`, `_clamp_int`, and the defaults merging — and drop everything that
served the filesystem watch. Add zip unpacking:

```python
"""Turning an uploaded batch file into jobs.

This was a folder watcher: files appeared on disk and a poll picked them up
exactly once, which is why it carried a seen-set, a wait window for images that
had not landed yet, and a replay buffer so two browser tabs would not race to
consume the same event. An upload has none of those problems - the whole file
arrives in one request, from one user, with the images inside it.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

from . import storage as storage_mod

log = logging.getLogger("h3studio.batch")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
BATCH_SUFFIXES = {".txt", ".json"}
ARCHIVE_SUFFIXES = {".zip"}

MAX_JOBS_PER_BATCH = 200
MAX_ZIP_ENTRY_BYTES = 32 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 512 * 1024 * 1024


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _job(raw: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = {**defaults, **raw}
    mode = merged.get("mode")
    preset = merged.get("preset")
    images = merged.get("images") or merged.get("image") or []
    if isinstance(images, str):
        images = [images]
    return {
        "prompt": str(merged.get("prompt", "")).strip(),
        "seconds": _clamp_int(merged.get("seconds"), 4, 15, 10),
        "takes": _clamp_int(merged.get("takes"), 1, 10, 1),
        "mode": mode if mode in {"t2v", "i2v", "r2v"} else "i2v",
        "preset": preset if isinstance(preset, str) and preset else "final",
        "ref_names": [Path(str(i)).name for i in images],
    }


def parse(text: str, suffix: str) -> list[dict[str, Any]]:
    """One batch file to a list of job descriptions.

    Raises ValueError with a message meant for the person who wrote the file.
    """
    if suffix == ".json":
        jobs = _parse_json(text)
    else:
        jobs = [_job({"prompt": line.strip()}, {})
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    jobs = [j for j in jobs if j["prompt"]]
    if len(jobs) > MAX_JOBS_PER_BATCH:
        raise ValueError(f"that batch has {len(jobs)} prompts; the limit is "
                         f"{MAX_JOBS_PER_BATCH}")
    return jobs


def _parse_json(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e}") from e

    # Accept a bare list of jobs, or of plain strings, as well as the documented
    # {"defaults":..., "jobs":[...]} shape. Exporters are inconsistent and there is
    # no reason to reject a form whose intent is unambiguous.
    if isinstance(data, list):
        defaults: dict[str, Any] = {}
        raw = data
    elif isinstance(data, dict):
        defaults = data.get("defaults") or {}
        raw = data.get("jobs") or []
    else:
        raise ValueError("expected a list of prompts or an object with 'jobs'")

    out = []
    for item in raw:
        if isinstance(item, str):
            out.append(_job({"prompt": item}, defaults))
        elif isinstance(item, dict):
            out.append(_job(item, defaults))
        else:
            raise ValueError(f"a job must be a string or an object, not {type(item).__name__}")
    return out


async def unpack_zip(data: bytes, user_id: str,
                     storage: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Read one archive: images to the object store, batch files to jobs.

    Entry names are reduced to their basename before anything is stored, because a
    zip is allowed to contain `../../etc/passwd` and this one arrived from a
    browser.
    """
    jobs: list[dict[str, Any]] = []
    images: dict[str, str] = {}
    total = 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"that .zip could not be opened: {e}") from e

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            suffix = Path(name).suffix.lower()
            if info.file_size > MAX_ZIP_ENTRY_BYTES:
                log.warning("skipping oversized zip entry %s", name)
                continue
            total += info.file_size
            if total > MAX_ZIP_TOTAL_BYTES:
                raise ValueError("that archive unpacks to more than 512 MB")
            payload = zf.read(info)
            if suffix in IMAGE_SUFFIXES:
                key = storage_mod.upload_key(user_id, suffix)
                await storage.put(key, payload, "application/octet-stream")
                images[name] = key
            elif suffix in BATCH_SUFFIXES:
                jobs += parse(payload.decode("utf-8-sig", "replace"), suffix)
    return jobs, images
```

- [ ] **Step 4: Add the upload route**

Append to `app/routes/media.py`:

```python
@router.post("/inbox/upload")
async def upload_batch(request: Request, file: UploadFile = File(...),
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """Drop a zip or batch file onto the page.

    Queuing is what dropping a batch in is meant to do; the pod policy still
    governs whether a GPU actually starts, and the budget ceiling remains the
    backstop either way.
    """
    name = Path(file.filename or "dropped").name
    suffix = Path(name).suffix.lower()
    accepted = batch.ARCHIVE_SUFFIXES | batch.BATCH_SUFFIXES | batch.IMAGE_SUFFIXES
    if suffix not in accepted:
        raise HTTPException(400, f"{name}: H3 Studio takes images, .zip archives, "
                                 f"or .txt/.json batch files")
    data = await file.read()
    store = request.app.state.storage
    cfg = request.app.state.cfg

    if suffix in batch.IMAGE_SUFFIXES:
        key = storage_mod.upload_key(user["id"], suffix)
        await store.put(key, data, file.content_type or "image/png")
        return {"queued": 0, "images": {name: key}}

    try:
        if suffix in batch.ARCHIVE_SUFFIXES:
            parsed, images = await batch.unpack_zip(data, user["id"], store)
        else:
            parsed = batch.parse(data.decode("utf-8-sig", "replace"), suffix)
            images = {}
    except ValueError as e:
        raise HTTPException(400, f"{name}: {e}")

    missing: list[str] = []
    queued = 0
    for job in parsed:
        refs = []
        for ref_name in job["ref_names"]:
            if key := images.get(ref_name):
                refs.append(key)
            else:
                missing.append(ref_name)
        preset = job["preset"] if job["preset"] in cfg.generation.presets \
            else cfg.generation.default_preset
        for _ in range(job["takes"]):
            await jobs_store.add(user["id"], job["prompt"], seconds=job["seconds"],
                                 ref_images=refs, mode=job["mode"], preset=preset)
            queued += 1
    return {"queued": queued, "images": images, "missing_images": sorted(set(missing))}
```

Add `from .. import batch` and `from ..store import jobs as jobs_store` to that
module's imports.

- [ ] **Step 5: Delete the watcher**

```bash
git rm app/inbox.py
```

- [ ] **Step 6: Run the batch tests and the whole suite**

Run: `python -m pytest -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/batch.py app/routes/media.py tests/test_batch.py
git rm --cached app/inbox.py 2>/dev/null; git add -A
git commit -m "Take batches by upload instead of by watching a folder

The watcher's seen-set, its wait window for images that had not landed, and the
45-second replay buffer that stopped two browser tabs racing for the same event
all existed to make a filesystem poll idempotent. An upload arrives whole, once,
from one user, with its images inside it - so all of that goes.

Zip entries are reduced to their basename before anything is stored: an archive
is allowed to contain ../../etc/passwd and this one came from a browser."
```

---

### Task 13: The pages

**Files:**
- Create: `web/login.html`, `web/auth.js`, `web/archive.html`, `web/archive.js`, `web/admin.html`, `web/admin.js`
- Modify: `web/index.html`, `web/app.js`, `web/style.css`

**Interfaces:**
- Consumes: the routes from Tasks 9, 10 and 12.
- Produces: nothing importable by Python.

- [ ] **Step 1: Write `web/auth.js`, shared by every page**

```javascript
// One fetch wrapper for every page. A 401 anywhere means the session is gone,
// and the only useful response is the login screen - handling that per call site
// would mean handling it wrongly in the one place someone forgets.
export async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (res.status === 401 && !location.pathname.startsWith('/login')) {
    location.href = '/login';
    throw new Error('signed out');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.status === 204 ? null : res.json();
}

export async function signOut() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
  location.href = '/login';
}
```

- [ ] **Step 2: Write `web/login.html`**

```html
<!doctype html>
<meta charset="utf-8">
<title>Sign in · H3 Studio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/static/favicon.png">
<link rel="stylesheet" href="/static/style.css">
<main class="login">
  <img src="/static/logo.png" alt="H3 Studio" class="login-logo">
  <form id="loginForm" class="card">
    <label>Email<input id="email" type="email" autocomplete="username" required></label>
    <label>Password<input id="password" type="password"
                          autocomplete="current-password" required></label>
    <button class="btn-primary" type="submit">Sign in</button>
    <p id="loginError" class="banner banner-error" hidden></p>
  </form>
</main>
<script type="module" src="/static/loginpage.js"></script>
```

Create `web/loginpage.js`:

```javascript
const form = document.getElementById('loginForm');
const err = document.getElementById('loginError');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  err.hidden = true;
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: document.getElementById('email').value,
      password: document.getElementById('password').value,
    }),
  });
  if (res.ok) { location.href = '/'; return; }
  const body = await res.json().catch(() => ({}));
  err.textContent = body.detail || 'Could not sign in.';
  err.hidden = false;
});
```

Add `loginpage.js` to the fingerprinted asset list in `main.py`'s `_page`.

- [ ] **Step 3: Rework `web/index.html`**

- Delete the `#quit` button, the output-folder rows in the settings modal, and
  both RunPod key panels (`#setupCard`, `#keyState`, `#saveKey`, `#saveKey2`,
  `#changeKey`, `#openFolder`, `#saveFolder`, `#openInbox`).
- Add to `.topbar`: `<span id="who"></span>`, a link `<a href="/archive">Archive</a>`,
  an admin-only link `<a href="/admin" id="adminLink" hidden>Admin</a>`, and a
  `<button id="signout" class="icon-btn">Sign out</button>`.
- Change the script tag to `<script type="module" src="/static/app.js"></script>`.

- [ ] **Step 4: Rework `web/app.js`**

- Import the wrapper: `import { api, signOut } from '/static/auth.js';` and delete
  the local `api()` helper, keeping every call site.
- On the first status response, fill `#who` with `user.email` and unhide
  `#adminLink` when `user.role === 'admin'`.
- Replace uses of `job.output_path` with `/api/video/${job.id}`.
- Replace upload handling: `POST /api/upload` now returns `{key, name}`; store the
  `key` in `ref_images` and preview it with `/api/image/${key}`.
- Delete `inbox_new`, `inbox_batches` and `inbox_waiting` handling from the status
  poll; `POST /api/inbox/upload` now returns `{queued, images, missing_images}`
  synchronously, so show that result directly on the drop.
- Delete the quit handler, the folder pickers and the key form handlers.
- Render `snapshot_for`'s new shape: `pod.state`, `counts`, and for a queued job
  `queue.total_queued` as `waiting for GPU · N in the queue`.

- [ ] **Step 5: Write `web/archive.html` and `web/archive.js`**

```html
<!doctype html>
<meta charset="utf-8">
<title>Archive · H3 Studio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/static/favicon.png">
<link rel="stylesheet" href="/static/style.css">
<header class="topbar">
  <a href="/" class="brand"><img src="/static/logo.png" alt="H3 Studio"></a>
  <nav><a href="/">Studio</a><a href="/archive" class="active">Archive</a>
       <a href="/admin" id="adminLink" hidden>Admin</a></nav>
  <span id="who"></span>
  <button id="signout" class="icon-btn" title="Sign out">⏻</button>
</header>
<main class="archive">
  <div id="grid" class="clip-grid"></div>
  <button id="more" class="btn" hidden>Load more</button>
  <p id="empty" class="empty" hidden>Nothing here yet. Clips you finish will appear here.</p>
</main>
<script type="module" src="/static/archive.js"></script>
```

```javascript
import { api, signOut } from '/static/auth.js';

const grid = document.getElementById('grid');
const more = document.getElementById('more');
let cursor = null;

function card(clip) {
  const el = document.createElement('figure');
  el.className = 'clip';
  // preload="none" matters: an archive page of forty clips would otherwise open
  // forty range requests against the app before the user has clicked anything.
  el.innerHTML = `
    <video src="${clip.video_url}" controls preload="none" playsinline></video>
    <figcaption>
      <p class="prompt"></p>
      <span class="meta">${clip.seconds}s · ${clip.preset} · ${clip.mode}</span>
      <a class="btn" href="${clip.video_url}" download>Download</a>
    </figcaption>`;
  el.querySelector('.prompt').textContent = clip.prompt;   // never innerHTML
  return el;
}

async function load() {
  const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
  const body = await api(`/api/archive${q}`);
  body.clips.forEach((c) => grid.append(card(c)));
  cursor = body.next_cursor;
  more.hidden = !cursor;
  document.getElementById('empty').hidden = grid.childElementCount > 0;
}

more.addEventListener('click', load);
document.getElementById('signout').addEventListener('click', signOut);

api('/api/me').then((me) => {
  document.getElementById('who').textContent = me.email;
  if (me.role === 'admin') document.getElementById('adminLink').hidden = false;
});
load();
```

The prompt is set with `textContent`, never `innerHTML`: prompts are arbitrary
user text and this page renders them.

- [ ] **Step 6: Write `web/admin.html` and `web/admin.js`**

`web/admin.html` has four `<section>`s — `#users`, `#gpu`, `#key`, `#runs` —
each holding a `<table>` or form with the ids used below, plus the same topbar as
`archive.html`.

```javascript
import { api, signOut } from '/static/auth.js';

const $ = (id) => document.getElementById(id);

function cell(row, text) {
  const td = row.insertCell();
  td.textContent = text;          // every value here came from someone's keyboard
  return td;
}

async function renderUsers() {
  const { users } = await api('/api/admin/users');
  const body = $('userRows');
  body.textContent = '';
  for (const u of users) {
    const row = body.insertRow();
    cell(row, u.email);
    cell(row, u.role);
    cell(row, u.is_active ? 'active' : 'disabled');
    const actions = row.insertCell();

    const toggle = document.createElement('button');
    toggle.className = 'btn';
    toggle.textContent = u.is_active ? 'Disable' : 'Enable';
    toggle.onclick = async () => {
      await api(`/api/admin/users/${u.id}`, {
        method: 'PATCH', body: JSON.stringify({ is_active: !u.is_active }) });
      renderUsers();
    };

    const reset = document.createElement('button');
    reset.className = 'btn';
    reset.textContent = 'Reset password';
    reset.onclick = async () => {
      const pw = prompt(`New password for ${u.email} (8 characters or more)`);
      if (!pw) return;
      await api(`/api/admin/users/${u.id}`, {
        method: 'PATCH', body: JSON.stringify({ password: pw }) });
      alert('Password changed. Hand it over yourself - it is not emailed.');
    };

    actions.append(toggle, reset);
  }
}

$('newUser').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await api('/api/admin/users', {
      method: 'POST',
      body: JSON.stringify({ email: $('newEmail').value,
                             password: $('newPassword').value,
                             role: $('newRole').value }),
    });
    e.target.reset();
    renderUsers();
  } catch (err) {
    $('userError').textContent = err.message;
    $('userError').hidden = false;
  }
});

async function renderGpu() {
  const s = await api('/api/admin/status');
  $('podState').textContent = `${s.pod.state}${s.pod.detail ? ' - ' + s.pod.detail : ''}`;
  $('podGpu').textContent = s.pod.gpu || '-';
  $('sessionCost').textContent =
    `$${s.session.cost_usd.toFixed(2)} of $${s.session.limit_usd.toFixed(2)}`;
  $('leaderWarning').hidden = s.leader;
  for (const input of document.querySelectorAll('input[name=policy]')) {
    input.checked = input.value === s.policy;
    input.onchange = async () => {
      await api('/api/admin/policy', {
        method: 'POST', body: JSON.stringify({ policy: input.value }) });
      renderGpu();
    };
  }
}

$('saveKey').addEventListener('click', async () => {
  const note = $('keyNote');
  try {
    const r = await api('/api/admin/runpod-key', {
      method: 'POST', body: JSON.stringify({ key: $('keyInput').value }) });
    $('keyInput').value = '';
    note.textContent = `Verified and saved (ends ${r.hint}). ${r.note}`;
  } catch (err) {
    note.textContent = err.message;
  }
  note.hidden = false;
});

$('saveBudget').addEventListener('click', async () => {
  await api('/api/admin/budget', {
    method: 'POST',
    body: JSON.stringify({ session_limit_usd: Number($('budgetInput').value) }) });
  renderGpu();
});

async function renderRuns() {
  const { runs, total_cost_usd } = await api('/api/admin/runs');
  const body = $('runRows');
  body.textContent = '';
  for (const r of runs) {
    const row = body.insertRow();
    cell(row, new Date(r.started_at * 1000).toLocaleString());
    cell(row, r.gpu_type || '-');
    cell(row, r.status);
    cell(row, `$${Number(r.cost_estimate).toFixed(4)}`);
    cell(row, r.note || '');
  }
  $('totalCost').textContent = `$${total_cost_usd.toFixed(2)}`;
}

$('signout').addEventListener('click', signOut);
api('/api/me').then((me) => { $('who').textContent = me.email; });
renderUsers();
renderGpu();
renderRuns();
// The pod state moves on its own, so this one panel polls; the user and run
// tables only change when someone on this page changes them.
setInterval(renderGpu, 5000);
```

- [ ] **Step 7: Style the new pages**

Add to `web/style.css`: `.login` (centred card, max-width 22rem), `.clip-grid`
(CSS grid, `repeat(auto-fill, minmax(18rem, 1fr))`), `.clip video`
(`width: 100%; aspect-ratio: 16/9; background: #000`), `.admin table`, and
`.topbar nav a.active`. Reuse the existing `--` custom properties; do not
introduce a second palette.

- [ ] **Step 8: Verify by hand against the mock backend**

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/h3_test \
SESSION_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))") \
S3_ENDPOINT= S3_BUCKET=h3-dev S3_ACCESS_KEY=x S3_SECRET_KEY=y \
OUTPUT_SINK=local LOCAL_OUTPUT_FOLDER=/tmp/h3out COOKIE_SECURE=false \
MOCK=true POD_POLICY=auto ADMIN_EMAIL=me@local ADMIN_PASSWORD=passphrase-1 \
python -m app.main
```

Then check, in a browser at `http://localhost:8777`:
1. `/` redirects to `/login` when signed out.
2. Signing in as `me@local` lands on the studio.
3. Queueing a prompt produces a clip within about ten seconds.
4. The clip plays inline and seeks (scrub it — a broken Range shows up here).
5. `/archive` lists it; Download saves a playable MP4.
6. `/admin` creates a second user; signing in as them shows an empty feed.
7. Signing in as the second user and opening `/admin` gets a 403 message, not a panel.

- [ ] **Step 9: Commit**

```bash
git add web/
git commit -m "Add the login, archive and admin pages

Every page shares one fetch wrapper, so a 401 always means the same thing -
handling it per call site would mean handling it wrongly in the one place
somebody forgets.

Archive videos are preload=\"none\": forty clips would otherwise open forty range
requests against the app before anyone clicks play. Prompts are rendered with
textContent, since they are arbitrary text from another user's keyboard."
```

---
### Task 14: Remove the desktop build

**Files:**
- Delete: `app/launch.py`, `app/mcp_server.py`, `app/install_mcp.py`, `H3 Studio.bat`, `H3 Studio (Demo).bat`, `h3studio.ico`, `config.example.yaml`
- Modify: `app/doctor.py`, `app/smoketest.py`, `app/calibrate.py`, `app/killpods.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `app.settings.get_settings`, `app.config.Config.from_settings`.
- Produces: the four operator scripts keep their current command-line behaviour, reading configuration from the environment instead of `config.yaml`.

- [ ] **Step 1: Delete the desktop entry points**

```bash
git rm app/launch.py app/mcp_server.py app/install_mcp.py h3studio.ico config.example.yaml
git rm "H3 Studio.bat" "H3 Studio (Demo).bat"
```

`mcp_server.py` and `install_mcp.py` go with them: they exist so Claude Desktop
can drive a *local* instance, and there is no local instance any more.

- [ ] **Step 2: Repoint the operator scripts**

In each of `app/doctor.py`, `app/smoketest.py`, `app/calibrate.py` and
`app/killpods.py`, replace

```python
cfg = config_mod.load()
```

with

```python
cfg = config_mod.Config.from_settings(get_settings())
```

and replace any `from . import db` with the `app.store` equivalents. Where a
script calls `cfg.save()` (`calibrate.py` writes its measurement back), write to
`kv` instead:

```python
    await kv.set("measured_minutes_per_clip", str(minutes))
    await kv.set("measured_on_gpu", gpu)
```

`estimate.py` is synchronous and stays that way; it keeps reading
`cfg.measured_minutes_per_clip`. The values are loaded from `kv` into the
`Config` object once, in `create_app`'s lifespan, alongside the RunPod key:

```python
        if measured := await kv.get("measured_minutes_per_clip"):
            cfg.measured_minutes_per_clip = float(measured)
        cfg.measured_on_gpu = await kv.get("measured_on_gpu")
```

A measurement that changes between deploys is not worth an async call on every
estimate, and `estimate.py` has no business knowing there is a database.

- [ ] **Step 3: Update `.gitignore`**

Remove `config.yaml`, `data/`, `out/` and `inbox/` entries if present, since none
of those paths exist now; add `.env`.

- [ ] **Step 4: Check nothing still imports what was deleted**

Run: `grep -rn "launch\|mcp_server\|install_mcp\|config_mod.load\|config.yaml\|from \. import db" app/ tests/ web/`
Expected: no matches.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Remove the desktop build

The launcher, the two .bat files and the MCP server all exist to run and drive a
copy of H3 Studio on someone's own machine. The moment this is a shared site
none of them have anything to point at, and keeping them would mean writing
every feature twice - once against SQLite with no login, once against Postgres
with one."
```

---

### Task 15: Container, compose, and the deploy runbook

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example`
- Create: `docs/DEPLOY.md`

**Interfaces:**
- Consumes: `app.main:main`.
- Produces: an image that starts the app, applies migrations, and answers `/api/health`.

- [ ] **Step 1: Write `.dockerignore`**

```
.git
.gitignore
__pycache__
*.pyc
.pytest_cache
.venv
venv
data
out
inbox
docs
tests
pytest.ini
requirements-dev.txt
*.md
```

- [ ] **Step 2: Write the `Dockerfile`**

```dockerfile
FROM python:3.12-slim

# ffmpeg is not optional: the audio strip remuxes finished clips with it, and the
# mock backend renders real MP4s with it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies before source, so editing a route does not re-resolve pip.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

# Non-root: nothing here needs to write to the image, and the object store holds
# everything that persists.
RUN useradd --create-home --uid 10001 h3 && chown -R h3:h3 /app
USER h3

EXPOSE 8777

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8777/api/health || exit 1

CMD ["python", "-m", "app.main"]
```

- [ ] **Step 3: Write `docker-compose.yml`**

```yaml
# For Dokploy. Postgres and the S3 endpoint already exist as their own services;
# they are referenced here, not defined.
services:
  h3-studio:
    build: .
    restart: unless-stopped
    # Exactly one. The orchestrator owns a rented GPU and a second replica would
    # start a second pod - nothing would error, the bill would double.
    deploy:
      replicas: 1
    ports:
      - "8777:8777"
    environment:
      DATABASE_URL: ${DATABASE_URL}
      SESSION_SECRET: ${SESSION_SECRET}
      S3_ENDPOINT: ${S3_ENDPOINT}
      S3_BUCKET: ${S3_BUCKET}
      S3_ACCESS_KEY: ${S3_ACCESS_KEY}
      S3_SECRET_KEY: ${S3_SECRET_KEY}
      S3_REGION: ${S3_REGION:-us-east-1}
      ADMIN_EMAIL: ${ADMIN_EMAIL}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD}
      RUNPOD_API_KEY: ${RUNPOD_API_KEY:-}
      POD_POLICY: ${POD_POLICY:-off}
      BUDGET_SESSION_LIMIT_USD: ${BUDGET_SESSION_LIMIT_USD:-8}
      MOCK: ${MOCK:-false}
      COOKIE_SECURE: ${COOKIE_SECURE:-true}
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8777/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 40s
```

- [ ] **Step 4: Write `.env.example`**

```bash
# Required
DATABASE_URL=postgresql://h3:CHANGE_ME@h3-postgres:5432/h3studio
SESSION_SECRET=            # python -c "import secrets;print(secrets.token_urlsafe(48))"
S3_ENDPOINT=http://minio:9000
S3_BUCKET=h3-studio
S3_ACCESS_KEY=
S3_SECRET_KEY=

# First admin, created only when the users table is empty
ADMIN_EMAIL=you@example.com
ADMIN_PASSWORD=

# Optional
S3_REGION=us-east-1
RUNPOD_API_KEY=            # can also be set from the admin page afterwards
POD_POLICY=off             # off | auto | keep-warm
BUDGET_SESSION_LIMIT_USD=8
MOCK=false
COOKIE_SECURE=true         # false only for plain-HTTP local development
```

- [ ] **Step 5: Build and smoke-test the image locally**

```bash
docker build -t h3-studio:test .
docker run --rm -p 8778:8777 \
  -e DATABASE_URL=postgresql://postgres:postgres@host.docker.internal:5433/h3_test \
  -e SESSION_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))") \
  -e S3_ENDPOINT= -e S3_BUCKET=h3 -e S3_ACCESS_KEY=x -e S3_SECRET_KEY=y \
  -e OUTPUT_SINK=local -e MOCK=true -e COOKIE_SECURE=false \
  -e ADMIN_EMAIL=me@local -e ADMIN_PASSWORD=passphrase-1 \
  h3-studio:test
```

Then: `curl -fsS http://localhost:8778/api/health` → `{"ok":true,"leader":true}`

- [ ] **Step 6: Write `docs/DEPLOY.md`**

Cover, in this order:
1. **What you need first** — a Postgres service and an S3 bucket in Dokploy, and the credentials for each.
2. **The environment table** — every variable from `.env.example`, whether it is required, and what it does.
3. **Creating the application** — source `github.com/CortexIL/h3-studio`, branch `master`, build type Dockerfile, port 8777, domain, HTTPS on.
4. **Replicas must stay at 1**, and why: the orchestrator owns a rented GPU, and a second replica means a second pod billing. The advisory lock stops the second one working, so scaling up buys nothing and risks a stuck queue.
5. **First sign-in** — the `ADMIN_EMAIL` / `ADMIN_PASSWORD` bootstrap, and that it only fires when the users table is empty. Change the password from `/admin` after the first sign-in, and clear `ADMIN_PASSWORD` from the environment.
6. **Turning the GPU on** — `POD_POLICY` starts `off`; set the RunPod key and switch policy to `auto` from `/admin`, then redeploy once so the live backend is constructed.
7. **Adding users** — `/admin` → Users → Create.
8. **Backups** — Postgres holds accounts and job history; S3 holds the clips. Both need backing up; losing either alone leaves the other useless.
9. **Rolling back** — redeploy the previous commit. Migrations are additive and never dropped, so an older image runs against a newer schema.
10. **Where the money goes** — the budget ceiling, the idle shutdown, and how to check `/admin` → Runs.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml .env.example docs/DEPLOY.md
git commit -m "Containerise for Dokploy

One replica, pinned in compose and in the runbook. The orchestrator owns a
rented GPU; a second replica would start a second pod and nothing would error.
The advisory lock makes that safe rather than merely documented.

ffmpeg is installed rather than made optional: the audio strip remuxes finished
clips with it and the mock backend renders with it."
```

---

### Task 16: `CLAUDE.md`

**Files:**
- Create: `CLAUDE.md`
- Modify: `README.md`, `STATUS.md`

**Interfaces:** none.

- [ ] **Step 1: Write `CLAUDE.md`**

It must describe the repository as it is *after* this plan, not the desktop app.
Cover:

- **What this is** — a hosted multi-user front end for running MiniMax H3 on a rented RunPod GPU. Users queue prompts; one shared pod renders them; clips land in S3.
- **Run it locally** — the `MOCK=true`, `OUTPUT_SINK=local` command from Task 13 Step 8, and the test-Postgres `docker run`.
- **Test** — `python -m pytest`. Tests need Postgres on 5433; the suite skips with instructions if it is missing.
- **Layout** — a table of `app/` modules and what each owns, matching the File Structure section of this plan.
- **The rules that are not obvious from the code:**
  - Every `/api` route carries an auth dependency explicitly. Never add a route without one; `tests/test_isolation.py` walks the route table and will fail.
  - Another user's resource is 404, never 403.
  - `jobs.get_for(user_id, id)` in routes; `jobs.get_any(id)` only in the orchestrator.
  - One uvicorn worker, one orchestrator, one pod. The advisory lock enforces it; do not remove it.
  - Nothing writes to disk at runtime. Runtime-settable configuration goes in `kv`.
  - Never log the RunPod key, a password, or a session cookie.
  - Presets and the weight manifest are code, not environment: they describe the model, not the deployment.
- **Money** — the budget ceiling and the idle shutdown are the only things standing between a bug and a bill. Any change to `orchestrator.py`'s policy machine needs the tests in `tests/test_orchestrator.py` to still pass.
- **Deploying** — points at `docs/DEPLOY.md`.
- **Design history** — points at `docs/superpowers/specs/`.

- [ ] **Step 2: Rewrite `README.md`**

Replace the desktop instructions (`double-click H3 Studio`, the RunPod key panel,
the Quit button, the output folder) with: what the service is, the architecture
diagram updated for Postgres and S3, how to sign in, how to queue, where clips
go, and a pointer to `docs/DEPLOY.md` for operators.

- [ ] **Step 3: Update `STATUS.md`**

Add a dated entry recording the move to a hosted multi-user service, and remove
statements that are no longer true (single user, SQLite, local output folder).

- [ ] **Step 4: Verify the claims**

Run every command `CLAUDE.md` and `README.md` tell a reader to run, and confirm
each does what the document says. A guide whose first command fails is worse than
no guide.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md STATUS.md
git commit -m "Document the repository as a hosted service

CLAUDE.md records the rules that are invisible in any single file: every route
carries its own auth dependency, another user's resource is 404 rather than 403,
and exactly one orchestrator may run because it owns a rented GPU."
```

---

### Task 17: Deploy

**Files:** none.

This task changes no code. It is listed so the deployment is done deliberately,
with the checks written down, rather than as an afterthought.

**Prerequisite:** the operator supplies the SSH host, user and port, or performs
the Dokploy steps themselves in the dashboard. Do not attempt to reach a remote
host without them.

- [ ] **Step 1: Push**

```bash
git push origin master
```

- [ ] **Step 2: Generate the secrets**

```bash
python -c "import secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('ADMIN_PASSWORD=' + secrets.token_urlsafe(18))"
```

Hand these to the operator. Do not commit them, and do not paste them into a file
in the repository.

- [ ] **Step 3: Create the Dokploy application**

Source: `github.com/CortexIL/h3-studio`, branch `master`, build type Dockerfile,
port 8777. Fill the environment from `.env.example`, pointing `DATABASE_URL` at
the existing Postgres service and `S3_*` at the existing bucket. Leave
`POD_POLICY=off` and `RUNPOD_API_KEY` empty for the first deploy. Set a domain and
enable HTTPS.

- [ ] **Step 4: Deploy and check the container came up**

Confirm, in order:
1. Logs show `applied migrations: 001_init.sql`.
2. Logs show `created the first admin account`.
3. `curl -fsS https://<domain>/api/health` returns `{"ok":true,"leader":true}`.
4. `/login` renders and the admin credentials work.
5. `/admin` lists exactly one user.

- [ ] **Step 5: Verify the database and bucket are actually being used**

```sql
SELECT count(*) FROM users;          -- 1
SELECT filename FROM schema_migrations;
```

- [ ] **Step 6: Go live on the GPU**

From `/admin`: paste the RunPod key (it is verified before it is stored), set the
budget ceiling, then set policy to `auto`. Redeploy once so the live backend is
constructed rather than the mock.

- [ ] **Step 7: Prove one clip end to end**

Create a second, non-admin user. Sign in as them, queue one short draft clip, and
confirm: it reports a queue position while the pod boots, the clip plays and
seeks, it appears in `/archive`, and the admin's Runs table shows the session with
a cost. Then confirm the pod terminates by itself after the idle window.

- [ ] **Step 8: Remove the bootstrap password**

Change the admin password from `/admin`, then clear `ADMIN_PASSWORD` from the
Dokploy environment and redeploy. The bootstrap only fires on an empty users
table, so leaving the variable set is a stored secret with no remaining purpose.

---

## Verification

Before calling this done:

```bash
python -m pytest -v          # every test, against a real Postgres
docker build -t h3-studio .  # the image builds
grep -rn "sqlite\|config.yaml\|output_path\|owner=" app/   # no matches
```

And by hand, on the deployed instance: two users cannot see each other's jobs,
archives, or clips; a normal user gets 403 from `/admin`; a disabled user is
signed out immediately; the pod shuts itself down when the queue empties.
