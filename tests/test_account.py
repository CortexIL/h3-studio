"""A signed-in user managing their own password and sessions."""
from __future__ import annotations

from app import auth
from app.store import users
from tests.conftest import sign_in

NEW = "a-brand-new-passphrase"


async def _change(client, current="passphrase-1", new=NEW):
    return await client.post("/api/me/password",
                             json={"current_password": current, "new_password": new})


async def test_changing_my_password_keeps_me_signed_in(client, db):
    await sign_in(client)
    assert (await _change(client)).status_code == 200
    assert (await client.get("/api/me")).status_code == 200


async def test_the_new_password_works_and_the_old_one_does_not(client, db):
    await sign_in(client)
    await _change(client)
    assert await users.authenticate("a@h3.local", NEW) is not None
    assert await users.authenticate("a@h3.local", "passphrase-1") is None


async def test_a_cookie_from_before_the_change_stops_working(client, db):
    await sign_in(client)
    old = client.cookies.get(auth.COOKIE_NAME)
    await _change(client)
    client.cookies.set(auth.COOKIE_NAME, old)
    assert (await client.get("/api/me")).status_code == 401


async def test_a_wrong_current_password_is_400_and_keeps_the_session(client, db):
    await sign_in(client)
    r = await _change(client, current="not-it")
    assert r.status_code == 400          # never 401: that means "signed out"
    assert (await client.get("/api/me")).status_code == 200


async def test_a_short_new_password_is_400(client, db):
    await sign_in(client)
    assert (await _change(client, new="short")).status_code == 400


async def test_guessing_the_current_password_is_rate_limited(client, db):
    await sign_in(client)
    codes = [(await _change(client, current=f"guess-{i}")).status_code for i in range(12)]
    assert 429 in codes


async def test_sign_out_everywhere_ends_every_session(client, db):
    await sign_in(client)
    old = client.cookies.get(auth.COOKIE_NAME)
    assert (await client.post("/api/me/sign-out-everywhere")).status_code == 200
    assert (await client.get("/api/me")).status_code == 401
    client.cookies.set(auth.COOKIE_NAME, old)
    assert (await client.get("/api/me")).status_code == 401
