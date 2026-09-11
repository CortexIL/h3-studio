"""The HTML pages are guarded on the server, not by their scripts.

A page that rendered first and redirected on its first failed API call showed
the studio for a moment to someone who was not signed in.
"""
from __future__ import annotations

import pytest

from app.store import users
from tests.conftest import sign_in


@pytest.mark.parametrize("path,location", [
    ("/", "/login"),
    ("/archive", "/login?next=/archive"),
    ("/admin", "/login?next=/admin"),
])
async def test_signed_out_is_redirected_before_any_html(client, db, path, location):
    r = await client.get(path)
    assert r.status_code == 303
    assert r.headers["location"] == location
    assert "<html" not in r.text.lower()


async def test_login_page_renders_when_signed_out(client, db):
    r = await client.get("/login")
    assert r.status_code == 200 and "Sign in" in r.text


async def test_signed_in_user_skips_the_login_page(client, db):
    await sign_in(client)
    r = await client.get("/login")
    assert r.status_code == 303 and r.headers["location"] == "/"


@pytest.mark.parametrize("path", ["/", "/archive"])
async def test_signed_in_user_gets_the_page(client, db, path):
    await sign_in(client)
    assert (await client.get(path)).status_code == 200


async def test_a_normal_user_is_sent_away_from_admin(client, db):
    await sign_in(client)
    r = await client.get("/admin")
    assert r.status_code == 303 and r.headers["location"] == "/"


async def test_an_admin_gets_the_admin_page(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    assert (await client.get("/admin")).status_code == 200


async def test_a_disabled_account_is_bounced_from_pages_too(client, db):
    u = await sign_in(client)
    await users.set_active(u["id"], False)
    r = await client.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"


async def test_redirects_are_never_cached(client, db):
    r = await client.get("/")
    assert r.headers.get("cache-control") == "no-store"
