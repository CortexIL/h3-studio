"""Fixtures shared by the whole suite.

The database is real Postgres rather than a stub: the code under test relies on
FOR UPDATE SKIP LOCKED, advisory locks and JSONB, none of which a fake reproduces,
and getting those wrong is exactly the class of bug worth catching.
"""
from __future__ import annotations

import getpass
import os

import psycopg
import pytest
import pytest_asyncio

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql://{getpass.getuser()}@localhost:5432/h3_test",
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
        reason=f"No Postgres at {TEST_DSN}. Create one:\n"
               "  createdb h3_test   (or set TEST_DATABASE_URL)"
    )
    for item in items:
        if "db" in getattr(item, "fixturenames", ()):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def dsn() -> str:
    return TEST_DSN


@pytest_asyncio.fixture
async def db(dsn):
    """A clean, migrated schema per test."""
    from app.store import pool as pool_mod

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await pool_mod.close_pool()
    await pool_mod.open_pool(dsn)
    async with pool_mod.connection() as conn:
        await pool_mod.migrate(conn)
    yield pool_mod
    await pool_mod.close_pool()
