import asyncio
import shutil

import psycopg

from app.store import jobs, projects, users
from app.store import pool as pool_mod


async def _u(name: str) -> str:
    return (await users.create(f"{name}@h3.local", "passphrase-1"))["id"]


async def test_create_makes_the_creator_its_owner(db):
    o = await _u("o")
    p = await projects.create(o, "School film")
    assert p["name"] == "School film"
    assert (await projects.get_for(o, p["id"]))["my_role"] == "owner"


async def test_a_stranger_or_a_bad_id_finds_nothing(db):
    o, s = await _u("o"), await _u("s")
    p = await projects.create(o, "School film")
    assert await projects.get_for(s, p["id"]) is None
    assert await projects.get_for(o, "not-a-uuid") is None


async def test_list_shows_my_projects_with_my_role_and_counts(db):
    o, e = await _u("o"), await _u("e")
    mine = await projects.create(o, "School film")
    theirs = await projects.create(e, "Ad")
    assert await projects.add_member(theirs["id"], o, "viewer") is True
    assert await projects.add_member(theirs["id"], o, "editor") is False
    await jobs.add(o, "a dog", project_id=mine["id"])
    rows = {r["name"]: r for r in await projects.list_for(o)}
    assert (rows["School film"]["my_role"], rows["School film"]["clips"]) == ("owner", 1)
    assert (rows["Ad"]["my_role"], rows["Ad"]["members"]) == ("viewer", 2)


async def test_my_clips_is_made_once_even_when_asked_twice_at_once(db):
    o = await _u("o")
    a, b = await asyncio.gather(projects.personal_for(o), projects.personal_for(o))
    assert a == b
    assert [r["name"] for r in await projects.list_for(o)] == ["My clips"]


async def test_a_clip_without_a_project_lands_in_my_clips(db):
    o = await _u("o")
    jid = await jobs.add(o, "a dog")
    assert (await jobs.get_any(jid))["project_id"] == await projects.personal_for(o)


async def test_deleting_a_project_takes_its_clips_and_members(db):
    o, e = await _u("o"), await _u("e")
    p = (await projects.create(o, "School film"))["id"]
    await projects.add_member(p, e, "editor")
    jid = await jobs.add(e, "a dog", project_id=p)
    await projects.delete(p)
    assert await jobs.get_any(jid) is None
    assert await projects.list_for(e) == []


async def test_the_migration_puts_old_clips_in_one_project_per_person(db, dsn, tmp_path, monkeypatch):
    # `db` is taken for its skip-without-Postgres and its teardown; the schema it
    # built is thrown away, because 018 must run over rows that predate it.
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    before = tmp_path / "before"
    before.mkdir()
    for f in sorted(pool_mod.MIGRATIONS.glob("*.sql")):
        if f.name < "018":
            shutil.copy(f, before / f.name)
    real = pool_mod.MIGRATIONS
    monkeypatch.setattr(pool_mod, "MIGRATIONS", before)
    await pool_mod.close_pool()
    await pool_mod.open_pool(dsn)
    async with pool_mod.connection() as conn:
        await pool_mod.migrate(conn)
    a, b, idle = await _u("a"), await _u("b"), await _u("idle")
    async with pool_mod.connection() as conn:
        for jid, uid in (("a1", a), ("a2", a), ("b1", b)):
            await conn.execute("INSERT INTO jobs (id, user_id, prompt) VALUES (%s,%s,'x')",
                               (jid, uid))
        await conn.commit()
    monkeypatch.setattr(pool_mod, "MIGRATIONS", real)
    async with pool_mod.connection() as conn:
        assert "018_projects.sql" in await pool_mod.migrate(conn)
    pa = {(await jobs.get_any(j))["project_id"] for j in ("a1", "a2")}
    pb = (await jobs.get_any("b1"))["project_id"]
    assert len(pa) == 1 and pb not in pa
    assert [(r["name"], r["my_role"]) for r in await projects.list_for(a)] == [("My clips", "owner")]
    assert await projects.list_for(idle) == []
