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


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("connection pool is not open")
    return _pool


@asynccontextmanager
async def connection():
    async with get_pool().connection() as conn:
        yield conn


async def migrate(conn) -> list[str]:
    """Apply pending .sql files in filename order.

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
            await (await conn.execute(
                "SELECT filename FROM schema_migrations")).fetchall()}
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
    row = await (await conn.execute(
        "SELECT pg_try_advisory_lock(%s) AS got", (key,))).fetchone()
    return bool(row["got"])
