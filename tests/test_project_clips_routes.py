"""Clip routes inside projects: who sees, who makes, who may change or delete."""
from __future__ import annotations

from app import storage as storage_mod
from app.store import jobs, projects, users
from tests.conftest import sign_in
from tests.test_avatar import _image
from tests.test_projects_routes import login

PNG = _image()


async def _team(client) -> dict:
    """o@ owns project P; e@ edits it, v@ watches it; s@ is a stranger."""
    t: dict = {}
    for key, name in (("o", "o"), ("e", "e"), ("v", "v"), ("s", "s")):
        if key == "o":
            t["o"] = (await sign_in(client, "o@h3.local"))["id"]
        else:
            t[key] = (await users.create(f"{name}@h3.local", "passphrase-1"))["id"]
    t["pid"] = (await client.post("/api/projects", json={"name": "P"})).json()["id"]
    for key, role in (("e", "editor"), ("v", "viewer")):
        r = await client.post(f"/api/projects/{t['pid']}/members",
                              json={"email": f"{key}@h3.local", "role": role})
        assert r.status_code == 200, r.text
    return t


async def _done_clip(client, maker_id, pid) -> str:
    store = client._transport.app.state.storage
    jid = await jobs.add(maker_id, "a clip", project_id=pid)
    key = storage_mod.video_key(maker_id, jid, "clip")
    await store.put(key, b"x" * 2048, "video/mp4")
    await jobs.update(jid, status="done", output_key=key, finished_at=1.0, output_bytes=2048)
    return jid


async def test_an_editors_clip_shows_up_in_the_owners_project_feed(client, db):
    t = await _team(client); await login(client, "e@h3.local")
    r = await client.post(f"/api/jobs?project={t['pid']}", json={"prompts": "a dog"})
    jid = r.json()["created"][0]
    await login(client, "o@h3.local")
    row = next(j for j in (await client.get(f"/api/jobs?project={t['pid']}")).json()["jobs"] if j["id"] == jid)
    assert (row["made_by"], row["can_manage"], row["project_id"]) == ("e@h3.local", True, t["pid"])


async def test_a_viewer_cannot_make_or_redo(client, db):
    t = await _team(client); jid = await _done_clip(client, t["o"], t["pid"])
    await login(client, "v@h3.local")
    assert (await client.post(f"/api/jobs?project={t['pid']}", json={"prompts": "x"})).status_code == 403
    for path in (f"/api/jobs/{jid}/again", f"/api/jobs/{jid}/upscale", f"/api/jobs/{jid}/extend-source"):
        assert (await client.post(path, json={})).status_code == 403, path
    assert (await client.post("/api/jobs/again", json={"ids": [jid]})).status_code == 404
    up = await client.post(f"/api/inbox/upload?project={t['pid']}",
                           files={"file": ("p.png", PNG, "image/png")})
    assert up.status_code == 403


async def test_a_viewer_can_watch_and_list(client, db):
    t = await _team(client); jid = await _done_clip(client, t["o"], t["pid"])
    await login(client, "v@h3.local")
    assert (await client.get(f"/api/video/{jid}")).status_code == 200
    assert (await client.get(f"/api/jobs/{jid}")).json()["can_manage"] is False
    clips = (await client.get(f"/api/archive?project={t['pid']}")).json()["clips"]
    assert [(c["id"], c["can_make"], c["can_manage"]) for c in clips] == [(jid, False, False)]
    ids = (await client.get(f"/api/archive/ids?project={t['pid']}")).json()["ids"]
    assert ids == [jid]


async def test_an_editor_redoes_a_teammates_clip_into_the_same_project(client, db):
    t = await _team(client); jid = await _done_clip(client, t["o"], t["pid"])
    await login(client, "e@h3.local")
    new = (await client.post(f"/api/jobs/{jid}/again")).json()["job_id"]
    assert (await jobs.get_any(new))["project_id"] == t["pid"]
    many = (await client.post("/api/jobs/again", json={"ids": [jid]})).json()["created"][0]
    row = await jobs.get_any(many)
    assert (row["project_id"], row["user_id"]) == (t["pid"], t["e"])


async def test_an_editor_cannot_change_a_teammates_clip(client, db):
    t = await _team(client); jid = await _done_clip(client, t["o"], t["pid"])
    await login(client, "e@h3.local")
    assert (await client.patch(f"/api/jobs/{jid}", json={"prompt": "mine"})).status_code == 403
    assert (await client.delete(f"/api/jobs/{jid}")).status_code == 403
    assert (await client.delete(f"/api/archive/{jid}")).status_code == 403
    assert (await client.post(f"/api/jobs/{jid}/retry")).status_code == 403
    assert (await client.post(f"/api/jobs/{jid}/cancel")).status_code == 403
    assert (await jobs.get_any(jid))["prompt"] != "mine"


async def test_bulk_delete_by_a_non_owner_leaves_a_teammates_file(client, db):
    t = await _team(client); jid = await _done_clip(client, t["o"], t["pid"])
    key = (await jobs.get_any(jid))["output_key"]
    store = client._transport.app.state.storage
    for email in ("e@h3.local", "v@h3.local"):
        await login(client, email)
        r = await client.post("/api/archive/delete", json={"ids": [jid]})
        assert r.json() == {"deleted": 0, "kept": 0}, email
        assert await store.head(key) is not None
        assert await jobs.get_any(jid) is not None


async def test_the_owner_deletes_an_editors_clip_and_its_file(client, db):
    t = await _team(client); jid = await _done_clip(client, t["e"], t["pid"])
    key = (await jobs.get_any(jid))["output_key"]
    await login(client, "o@h3.local")
    assert (await client.delete(f"/api/archive/{jid}")).status_code == 200
    assert await jobs.get_any(jid) is None
    assert await client._transport.app.state.storage.head(key) is None


async def test_the_owner_hides_and_removes_an_editors_entries(client, db):
    t = await _team(client)
    done = await _done_clip(client, t["e"], t["pid"])
    queued = await jobs.add(t["e"], "waiting", project_id=t["pid"])
    await login(client, "o@h3.local")
    assert (await client.delete(f"/api/jobs/{done}")).json()["hidden"] is True
    assert (await client.delete(f"/api/jobs/{queued}")).json()["hidden"] is False
    assert await jobs.get_any(queued) is None


async def test_a_stranger_gets_404_on_every_clip_route(client, db):
    t = await _team(client); jid = await _done_clip(client, t["o"], t["pid"])
    await login(client, "s@h3.local")
    for method, path in (("GET", f"/api/jobs/{jid}"), ("GET", f"/api/video/{jid}"),
                         ("PATCH", f"/api/jobs/{jid}"), ("DELETE", f"/api/jobs/{jid}"),
                         ("DELETE", f"/api/archive/{jid}"), ("POST", f"/api/jobs/{jid}/again"),
                         ("GET", f"/api/jobs?project={t['pid']}"),
                         ("POST", f"/api/jobs?project={t['pid']}")):
        r = await client.request(method, path, json={"prompts": "x"} if method != "GET" else None)
        assert r.status_code == 404, (method, path, r.status_code)


async def test_teammates_see_reference_pictures_strangers_do_not(client, db):
    t = await _team(client); await login(client, "o@h3.local")
    key = (await client.post("/api/upload", files={"file": ("p.png", PNG, "image/png")})).json()["key"]
    await client.post(f"/api/jobs?project={t['pid']}", json={"prompts": "a dog", "ref_images": [key]})
    await login(client, "e@h3.local"); assert (await client.get(f"/api/image/{key}")).status_code == 200
    await login(client, "s@h3.local"); assert (await client.get(f"/api/image/{key}")).status_code == 404


async def test_a_clip_sent_without_a_project_lands_in_my_clips(client, db):
    me = await sign_in(client, "new@h3.local")
    jid = (await client.post("/api/jobs", json={"prompts": "old tab"})).json()["created"][0]
    names = [p["name"] for p in (await client.get("/api/projects")).json()["projects"]]
    assert names == ["My clips"]
    assert (await jobs.get_any(jid))["project_id"] == await projects.personal_for(me["id"])


async def test_clear_finished_in_a_project_leaves_teammates_clips(client, db):
    t = await _team(client); theirs = await _done_clip(client, t["o"], t["pid"])
    await _done_clip(client, t["e"], t["pid"])
    await login(client, "e@h3.local")
    assert (await client.post(f"/api/jobs/clear-finished?project={t['pid']}")).json()["removed"] == 1
    ids = [j["id"] for j in (await client.get(f"/api/jobs?project={t['pid']}")).json()["jobs"]]
    assert theirs in ids
