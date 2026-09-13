from __future__ import annotations

import httpx
import pytest

from app.store import jobs, kv, users
from tests.conftest import sign_in

ADMIN_ROUTES = [
    ("GET", "/api/admin/users", None),
    ("POST", "/api/admin/users", {"email": "x@h3.local", "password": "passphrase-9"}),
    ("GET", "/api/admin/jobs", None),
    ("GET", "/api/admin/status", None),
    ("POST", "/api/admin/policy", {"policy": "off"}),
    ("POST", "/api/admin/sage", {"enabled": True}),
    ("POST", "/api/admin/budget", {"session_limit_usd": 5.0}),
    ("GET", "/api/admin/runs", None),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
async def test_normal_users_are_refused_everywhere(client, db, method, path, body):
    await sign_in(client, "u@h3.local", role="user")
    r = await client.request(method, path, json=body)
    assert r.status_code == 403, path


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
async def test_anonymous_is_refused_everywhere(client, db, method, path, body):
    r = await client.request(method, path, json=body)
    assert r.status_code == 401, path


async def test_admin_creates_a_user_who_can_sign_in(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    r = await client.post("/api/admin/users",
                          json={"email": "new@h3.local", "password": "passphrase-2"})
    assert r.status_code == 200
    assert await users.authenticate("new@h3.local", "passphrase-2") is not None


async def test_created_user_is_never_echoed_with_a_hash(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    r = await client.post("/api/admin/users",
                          json={"email": "new@h3.local", "password": "passphrase-2"})
    assert "password" not in r.text and "argon2" not in r.text


async def test_creating_a_duplicate_is_a_clean_400(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    body = {"email": "dup@h3.local", "password": "passphrase-2"}
    assert (await client.post("/api/admin/users", json=body)).status_code == 200
    r = await client.post("/api/admin/users", json=body)
    assert r.status_code == 400 and "already" in r.json()["detail"]


async def test_a_short_password_is_a_clean_400(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    r = await client.post("/api/admin/users",
                          json={"email": "new@h3.local", "password": "abc"})
    assert r.status_code == 400


async def test_admin_disables_a_user(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    u = await users.create("victim@h3.local", "passphrase-3")
    r = await client.patch(f"/api/admin/users/{u['id']}", json={"is_active": False})
    assert r.status_code == 200
    assert (await users.by_id(u["id"]))["is_active"] is False


async def test_admin_cannot_disable_themselves(client, db):
    me = await sign_in(client, "boss@h3.local", role="admin")
    r = await client.patch(f"/api/admin/users/{me['id']}", json={"is_active": False})
    assert r.status_code == 400
    assert (await users.by_id(me["id"]))["is_active"] is True


async def test_admin_cannot_demote_themselves(client, db):
    """Otherwise the instance can end up with no admin and no way back in."""
    me = await sign_in(client, "boss@h3.local", role="admin")
    r = await client.patch(f"/api/admin/users/{me['id']}", json={"role": "user"})
    assert r.status_code == 400
    assert (await users.by_id(me["id"]))["role"] == "admin"


async def test_admin_sees_every_job(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    other = await users.create("b@h3.local", "passphrase-2")
    await jobs.add(other["id"], "theirs")
    body = (await client.get("/api/admin/jobs")).json()
    assert [j["prompt"] for j in body["jobs"]] == ["theirs"]
    assert body["jobs"][0]["user_email"] == "b@h3.local"


async def test_setting_policy_persists(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    assert (await client.post("/api/admin/policy",
                              json={"policy": "keep-warm"})).status_code == 200
    assert await kv.get("pod_policy") == "keep-warm"


async def test_bad_policy_is_refused(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    r = await client.post("/api/admin/policy", json={"policy": "sometimes"})
    assert r.status_code == 400


async def test_budget_must_be_sane(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    assert (await client.post("/api/admin/budget",
                              json={"session_limit_usd": 0})).status_code == 400
    assert (await client.post("/api/admin/budget",
                              json={"session_limit_usd": 99999})).status_code == 400
    assert (await client.post("/api/admin/budget",
                              json={"session_limit_usd": 12.5})).status_code == 200
    assert await kv.get("budget_session_limit_usd") == "12.5"


class _Reply:
    def __init__(self, code: int) -> None:
        self.code = code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return httpx.Response(self.code, request=httpx.Request("GET", "http://x"))


async def test_runpod_key_is_verified_before_it_is_saved(client, db, monkeypatch):
    await sign_in(client, "boss@h3.local", role="admin")
    monkeypatch.setattr("app.routes.admin.httpx.AsyncClient",
                        lambda **k: _Reply(401))
    r = await client.post("/api/admin/runpod-key", json={"key": "rp-bogus"})
    assert r.status_code == 400
    assert await kv.get("runpod_api_key") is None


async def test_a_verified_key_is_stored_and_never_echoed(client, db, monkeypatch):
    await sign_in(client, "boss@h3.local", role="admin")
    monkeypatch.setattr("app.routes.admin.httpx.AsyncClient",
                        lambda **k: _Reply(200))
    r = await client.post("/api/admin/runpod-key", json={"key": "rp-secret-value"})
    assert r.status_code == 200
    assert r.json()["hint"] == "alue"
    assert "rp-secret-value" not in r.text
    assert await kv.get("runpod_api_key") == "rp-secret-value"


async def test_admin_status_carries_the_shared_view(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    body = (await client.get("/api/admin/status")).json()
    assert body["leader"] is True
    assert "policy" in body and "session" in body



async def test_the_user_list_carries_usage(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    other = await users.create("busy@h3.local", "passphrase-2")
    done = await jobs.add(other["id"], "done")
    await jobs.update(done, status="done", output_key="k", output_bytes=300, finished_at=1.0)
    await jobs.add(other["id"], "waiting")
    rows = (await client.get("/api/admin/users")).json()["users"]
    busy = next(r for r in rows if r["email"] == "busy@h3.local")
    assert busy["usage"]["done"] == 1 and busy["usage"]["queued"] == 1
    assert busy["usage"]["stored_bytes"] == 300
    boss = next(r for r in rows if r["email"] == "boss@h3.local")
    assert boss["usage"]["done"] == 0


async def test_an_admin_resetting_their_own_password_stays_signed_in(client, db):
    me = await sign_in(client, "boss@h3.local", role="admin")
    r = await client.patch(f"/api/admin/users/{me['id']}",
                           json={"password": "another-passphrase"})
    assert r.status_code == 200
    assert (await client.get("/api/me")).status_code == 200
