"""Who is on the app, and whether a running GPU is anybody's.

The point of the feature is a judgement an admin makes with real money at stake,
so the cases worth pinning down are the ones that would make it lie: a page left
open reading as work, a stretch of use that keeps counting after the person has
gone, and a GPU called idle while something is still queued.
"""
from __future__ import annotations

from app.store import jobs, pool, users
from tests.conftest import sign_in


async def _row(email: str) -> dict:
    user = await users.by_email(email)
    assert user is not None
    return user


async def _age(email: str, column: str, seconds: float) -> None:
    """Push one of a user's timestamps into the past, to stand in for waiting."""
    assert column in ("last_seen_at", "active_since", "last_action_at")
    async with pool.connection() as conn:
        await conn.execute(
            f"UPDATE users SET {column} = now() - make_interval(secs => %s::float8)"
            " WHERE email=%s", (float(seconds), email))
        await conn.commit()


async def test_a_request_marks_someone_here_and_starts_their_stretch(client, db):
    await sign_in(client, "dana@h3.local")
    assert (await _row("dana@h3.local"))["last_seen_at"] is None

    assert (await client.get("/api/jobs")).status_code == 200
    row = await _row("dana@h3.local")
    assert row["last_seen_at"] is not None
    assert row["active_since"] is not None
    # Reading is not doing: this is the column that separates someone working
    # from a tab left open in front of an empty chair.
    assert row["last_action_at"] is None


async def test_only_a_write_counts_as_doing_something(client, db):
    await sign_in(client, "dana@h3.local")
    await client.get("/api/jobs")
    assert (await _row("dana@h3.local"))["last_action_at"] is None

    r = await client.post("/api/jobs", json={"prompts": "a kite over the sea", "mode": "t2v"})
    assert r.status_code == 200, r.text
    assert (await _row("dana@h3.local"))["last_action_at"] is not None


async def test_a_stretch_continues_across_a_short_pause(client, db):
    await sign_in(client, "dana@h3.local")
    await client.get("/api/jobs")
    started = (await _row("dana@h3.local"))["active_since"]

    await _age("dana@h3.local", "last_seen_at", users.ACTIVE_GAP_SECONDS - 30)
    users.reset_touch_throttle()
    await client.get("/api/jobs")
    assert (await _row("dana@h3.local"))["active_since"] == started


async def test_coming_back_after_a_real_gap_starts_a_new_stretch(client, db):
    await sign_in(client, "dana@h3.local")
    await client.get("/api/jobs")
    started = (await _row("dana@h3.local"))["active_since"]

    await _age("dana@h3.local", "last_seen_at", users.ACTIVE_GAP_SECONDS + 60)
    users.reset_touch_throttle()
    await client.get("/api/jobs")
    # Otherwise "active for 9 hours" would mean a browser left open overnight.
    assert (await _row("dana@h3.local"))["active_since"] > started


async def test_the_admin_view_says_who_is_here_and_for_how_long(client, db):
    dana = await users.create("dana@h3.local", "passphrase-2")
    await sign_in(client, "boss@h3.local", role="admin")
    # Dana was here for ten minutes and left an hour ago.
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE users SET last_seen_at = now() - interval '1 hour',"
            " active_since = now() - interval '70 minutes',"
            " last_action_at = now() - interval '65 minutes' WHERE id=%s", (dana["id"],))
        await conn.commit()

    body = (await client.get("/api/admin/activity")).json()
    people = {p["email"]: p for p in body["people"]}

    assert people["boss@h3.local"]["here"] is True
    assert body["here_count"] == 1

    gone = people["dana@h3.local"]
    assert gone["here"] is False
    assert 3500 < gone["seen_s_ago"] < 3700
    # Ten minutes of use, not seventy: the stretch ended when she was last seen.
    assert 500 < gone["using_for_s"] < 700
    # Whoever is here sorts first, so an admin reads the live ones without hunting.
    assert body["people"][0]["email"] == "boss@h3.local"


async def test_someone_who_has_never_signed_in_has_no_times(client, db):
    await users.create("guest@h3.local", "passphrase-2")
    await sign_in(client, "boss@h3.local", role="admin")
    body = (await client.get("/api/admin/activity")).json()
    guest = next(p for p in body["people"] if p["email"] == "guest@h3.local")
    assert guest["here"] is False
    assert guest["seen_s_ago"] is None
    assert guest["using_for_s"] is None
    assert guest["acted_s_ago"] is None


async def test_no_pod_is_off_rather_than_idle(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    body = (await client.get("/api/admin/activity")).json()
    # POD_POLICY is off in the test settings, so nothing is being paid for -
    # which must not read as "up with nothing to do".
    assert body["pods_up"] == 0
    assert body["verdict"] == "off"


async def test_each_person_carries_their_own_share_of_the_queue(client, db):
    dana = await users.create("dana@h3.local", "passphrase-2")
    await jobs.add(dana["id"], "one")
    await jobs.add(dana["id"], "two")
    await sign_in(client, "boss@h3.local", role="admin")

    body = (await client.get("/api/admin/activity")).json()
    people = {p["email"]: p for p in body["people"]}
    assert people["dana@h3.local"]["queued"] == 2
    assert people["boss@h3.local"]["queued"] == 0
    assert body["queued"] == 2
