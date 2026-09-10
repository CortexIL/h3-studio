"""Every route that takes a job id, proven blind to other people's jobs.

Written as one table rather than a test per route so that adding a route without
adding it here is visible: the table is the checklist.
"""
from __future__ import annotations

import pytest

from app.store import jobs, users
from tests.conftest import sign_in

# (method, path template, json body)
JOB_ROUTES = [
    ("GET", "/api/jobs/{jid}", None),
    ("PATCH", "/api/jobs/{jid}", {"prompt": "stolen"}),
    ("DELETE", "/api/jobs/{jid}", None),
    ("POST", "/api/jobs/{jid}/retry", None),
    ("POST", "/api/jobs/{jid}/again", None),
    ("POST", "/api/jobs/{jid}/cancel", None),
    ("GET", "/api/video/{jid}", None),
]

SECRET = "the victim's secret prompt"


async def _victim_job() -> tuple[dict, str]:
    victim = await users.create("victim@h3.local", "passphrase-1")
    jid = await jobs.add(victim["id"], SECRET)
    await jobs.update(jid, status="done",
                      output_key=f"videos/{victim['id']}/{jid}.mp4",
                      finished_at=1.0)
    return victim, jid


async def _attacker(client) -> dict:
    return await sign_in(client, "attacker@h3.local", password="passphrase-2")


@pytest.mark.parametrize("method,path,body", JOB_ROUTES)
async def test_another_users_job_is_404_not_403(client, db, method, path, body):
    _, jid = await _victim_job()
    await _attacker(client)
    r = await client.request(method, path.format(jid=jid), json=body)
    # 403 would confirm the job exists.
    assert r.status_code == 404, f"{method} {path} returned {r.status_code}"
    assert SECRET not in r.text


@pytest.mark.parametrize("method,path,body", JOB_ROUTES)
async def test_anonymous_gets_401_everywhere(client, db, method, path, body):
    _, jid = await _victim_job()
    r = await client.request(method, path.format(jid=jid), json=body)
    assert r.status_code == 401, f"{method} {path} returned {r.status_code}"


async def test_a_failed_edit_does_not_change_the_victims_job(client, db):
    victim, jid = await _victim_job()
    await _attacker(client)
    await client.patch(f"/api/jobs/{jid}", json={"prompt": "stolen"})
    assert (await jobs.get_for(victim["id"], jid))["prompt"] == SECRET


async def test_a_failed_delete_does_not_remove_the_victims_job(client, db):
    victim, jid = await _victim_job()
    await _attacker(client)
    await client.delete(f"/api/jobs/{jid}")
    assert await jobs.get_for(victim["id"], jid) is not None


async def test_a_failed_cancel_does_not_stop_the_victims_job(client, db):
    victim, jid = await _victim_job()
    await _attacker(client)
    await client.post(f"/api/jobs/{jid}/cancel")
    assert (await jobs.get_for(victim["id"], jid))["status"] == "done"


async def test_reference_keys_from_another_prefix_are_dropped(client, db):
    victim = await users.create("victim@h3.local", "passphrase-1")
    attacker = await _attacker(client)
    r = await client.post("/api/jobs", json={
        "prompts": "borrowed",
        "ref_images": [f"uploads/{victim['id']}/theirs.png",
                       f"uploads/{attacker['id']}/mine.png"],
    })
    assert r.status_code == 200
    row = (await jobs.list_for(attacker["id"]))[0]
    assert row["ref_images"] == [f"uploads/{attacker['id']}/mine.png"]


async def test_a_patch_cannot_smuggle_in_another_prefix(client, db):
    victim = await users.create("victim@h3.local", "passphrase-1")
    attacker = await _attacker(client)
    jid = await jobs.add(attacker["id"], "mine")
    await client.patch(f"/api/jobs/{jid}",
                       json={"ref_images": [f"uploads/{victim['id']}/theirs.png"]})
    assert (await jobs.get_for(attacker["id"], jid))["ref_images"] == []


async def test_traversal_in_an_image_key_is_refused(client, db):
    attacker = await _attacker(client)
    r = await client.get(
        f"/api/image/uploads/{attacker['id']}/../../videos/x.mp4")
    assert r.status_code == 404


async def test_another_users_image_prefix_is_refused(client, db):
    victim = await users.create("victim@h3.local", "passphrase-1")
    await _attacker(client)
    r = await client.get(f"/api/image/uploads/{victim['id']}/theirs.png")
    assert r.status_code == 404


async def test_again_all_only_touches_my_own_jobs(client, db):
    victim, _ = await _victim_job()
    attacker = await _attacker(client)
    r = await client.post("/api/jobs/again-all", json={"status": "done"})
    assert r.json()["queued"] == 0
    assert await jobs.list_for(attacker["id"]) == []
    assert len(await jobs.list_for(victim["id"])) == 1


async def test_clear_finished_only_touches_my_own_jobs(client, db):
    victim, jid = await _victim_job()
    await _attacker(client)
    r = await client.post("/api/jobs/clear-finished")
    assert r.json()["removed"] == 0
    assert await jobs.get_for(victim["id"], jid) is not None


async def test_status_never_leaks_another_users_prompt(client, db):
    await _victim_job()
    await _attacker(client)
    assert SECRET not in (await client.get("/api/status")).text


async def test_the_archive_never_leaks_another_users_clip(client, db):
    await _victim_job()
    await _attacker(client)
    body = await client.get("/api/archive")
    assert body.json()["clips"] == []
    assert SECRET not in body.text


async def test_every_api_route_requires_a_session(client, db):
    """A route that forgot its dependency shows up here, not in production.

    Walked through the OpenAPI schema rather than app.routes: FastAPI wraps
    included routers, so app.routes no longer flattens to the real handlers.
    """
    from app.main import create_app
    from app.settings import get_settings

    schema = create_app(get_settings()).openapi()
    public = {("GET", "/api/health"), ("POST", "/api/auth/login"),
              ("POST", "/api/auth/logout")}
    checked = 0
    for path, operations in schema["paths"].items():
        if not path.startswith("/api"):
            continue
        url = (path.replace("{job_id}", "x").replace("{user_id}", "x")
                   .replace("{key}", "x"))
        for method in operations:
            verb = method.upper()
            if verb in {"HEAD", "OPTIONS"} or (verb, path) in public:
                continue
            r = await client.request(verb, url, json={})
            assert r.status_code in (401, 403), \
                f"{verb} {path} -> {r.status_code} without a session"
            checked += 1
    # A schema that silently listed nothing would make this test vacuous.
    assert checked > 15
