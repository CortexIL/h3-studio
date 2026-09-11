from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row


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
