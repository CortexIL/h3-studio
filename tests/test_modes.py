"""The mode registry, and the one copy of it that cannot import Python.

`app/modes.py` is the source for the route, the batch parser and the picker. The
CHECK constraint on jobs.mode is the exception - a migration cannot import code -
so the tests here are what keep the two in step. Getting it wrong is not a loud
failure: a mode Python accepts and the database rejects only shows up when someone
queues one.
"""
from __future__ import annotations

import psycopg
import pytest

from app import batch, modes
from app.routes import jobs as jobs_routes
from app.store import users


async def _insert(conn, job_id: str, user_id: str, mode: str) -> None:
    await conn.execute(
        "INSERT INTO jobs (id, user_id, prompt, mode) VALUES (%s, %s, %s, %s)",
        (job_id, user_id, "hi", mode))


async def test_the_database_allows_every_mode_we_know_about(db):
    u = await users.create("modes@h3.local", "passphrase-1")
    async with db.connection() as conn:
        for i, mode in enumerate(modes.KNOWN):
            await _insert(conn, f"j{i}", u["id"], mode)
        await conn.commit()


async def test_the_database_still_refuses_a_mode_nobody_has_heard_of(db):
    u = await users.create("modes2@h3.local", "passphrase-1")
    async with db.connection() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            await _insert(conn, "jx", u["id"], "x2v")


def test_the_route_and_the_batch_parser_read_the_same_list():
    """They used to keep separate copies, so a mode could be accepted by one and
    silently downgraded to the default by the other."""
    assert jobs_routes.MODES is modes.OFFERED
    assert batch.MODES is modes.OFFERED


def test_a_retired_mode_is_still_legal_but_never_offered():
    """Rows already carry r2v. It has to stay valid without coming back."""
    assert "r2v" in modes.KNOWN
    assert "r2v" not in modes.OFFERED


def test_every_mode_the_database_allows_has_something_to_call_it():
    for mode in modes.KNOWN:
        assert modes.label(mode) and modes.label(mode) != mode


def test_a_mode_from_a_newer_build_reads_as_itself_rather_than_as_nothing():
    """A rollback must not leave a blank label on a row a newer build wrote."""
    assert modes.label("v2v") == "v2v"


def test_the_default_is_offered():
    assert modes.DEFAULT_MODE in modes.OFFERED
