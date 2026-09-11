"""The pages are guarded on the server, not by the client.

A page that rendered first and redirected on its first failed API call showed
the studio for a moment to someone who was not signed in. Every route serves the
React app's index.html (a stub here - see the spa_dist fixture) with its own
title.
"""
from __future__ import annotations

import pytest

from app.store import users
from tests.conftest import sign_in


@pytest.mark.parametrize("path,location", [
    ("/", "/login"),
    ("/archive", "/login?next=/archive"),
    ("/account", "/login?next=/account"),
    ("/admin", "/login?next=/admin"),
])
async def test_signed_out_is_redirected_before_any_html(client, db, path, location):
    r = await client.get(path)
    assert r.status_code == 303
    assert r.headers["location"] == location
    assert "<html" not in r.text.lower()


async def test_login_page_renders_when_signed_out(client, db):
    r = await client.get("/login")
    assert r.status_code == 200
    assert "<title>Sign in · H3 Studio</title>" in r.text
    assert '<div id="root">' in r.text


async def test_signed_in_user_skips_the_login_page(client, db):
    await sign_in(client)
    r = await client.get("/login")
    assert r.status_code == 303 and r.headers["location"] == "/"


@pytest.mark.parametrize("path,title", [
    ("/", "H3 Studio"),
    ("/archive", "Archive · H3 Studio"),
    ("/account", "Account · H3 Studio"),
])
async def test_signed_in_user_gets_the_app_titled_for_the_route(client, db, path, title):
    await sign_in(client)
    r = await client.get(path)
    assert r.status_code == 200
    assert f"<title>{title}</title>" in r.text
    assert r.text.count("<title>") == 1


async def test_a_normal_user_is_sent_away_from_admin(client, db):
    await sign_in(client)
    r = await client.get("/admin")
    assert r.status_code == 303 and r.headers["location"] == "/"


async def test_an_admin_gets_the_admin_page(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    r = await client.get("/admin")
    assert r.status_code == 200 and "<title>Admin · H3 Studio</title>" in r.text


async def test_a_disabled_account_is_bounced_from_pages_too(client, db):
    u = await sign_in(client)
    await users.set_active(u["id"], False)
    r = await client.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"


async def test_redirects_are_never_cached(client, db):
    r = await client.get("/")
    assert r.headers.get("cache-control") == "no-store"


async def test_pages_are_never_cached_and_carry_security_headers(client, db):
    # The HTML names hashed asset files; a cached copy would outlive them.
    r = await client.get("/login")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin"


async def test_hashed_assets_are_cached_for_a_year(client, db):
    r = await client.get("/assets/app-3f9a1c.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_a_missing_asset_is_a_404_that_is_not_cached_forever(client, db):
    r = await client.get("/assets/app-gone.js")
    assert r.status_code == 404
    assert "immutable" not in r.headers.get("cache-control", "")


async def test_an_unknown_path_is_a_404_not_the_app(client, db):
    r = await client.get("/nowhere")
    assert r.status_code == 404
    assert '<div id="root">' not in r.text


async def test_a_missing_build_says_how_to_fix_it(client, db, spa_dist):
    (spa_dist / "index.html").unlink()
    r = await client.get("/login")
    assert r.status_code == 500 and "npm run build" in r.text
