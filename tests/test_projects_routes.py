"""Projects API: create, list, people, rename, delete, and who may do which."""
from __future__ import annotations

from app.store import jobs, users
from tests.conftest import sign_in
from tests.test_avatar import _image


async def login(client, email):
    r = await client.post("/api/auth/login", json={"email": email, "password": "passphrase-1"})
    assert r.status_code == 200, r.text


async def _owner_with_project(client) -> str:
    await sign_in(client, "o@h3.local")
    return (await client.post("/api/projects", json={"name": "School film"})).json()["id"]


async def _owner_and_member(client, role) -> tuple[str, str]:
    pid = await _owner_with_project(client)
    e = await users.create("e@h3.local", "passphrase-1")
    r = await client.post(f"/api/projects/{pid}/members", json={"email": "e@h3.local", "role": role})
    assert r.status_code == 200, r.text
    await login(client, "e@h3.local")
    return pid, e["id"]


async def test_create_and_list_my_projects(client, db):
    await sign_in(client, "o@h3.local")
    r = await client.post("/api/projects", json={"name": "  School film "})
    assert r.status_code == 200 and r.json()["name"] == "School film"
    listed = (await client.get("/api/projects")).json()["projects"]
    assert [(p["name"], p["my_role"]) for p in listed] == [("School film", "owner")]
    assert listed[0]["cover_url"] is None


async def test_a_blank_or_long_name_is_refused(client, db):
    await sign_in(client, "o@h3.local")
    for name in ("   ", "x" * 81):
        assert (await client.post("/api/projects", json={"name": name})).status_code == 400


async def test_a_teammate_added_by_email_sees_the_project(client, db):
    pid = await _owner_with_project(client)
    await users.create("e@h3.local", "passphrase-1")
    r = await client.post(f"/api/projects/{pid}/members", json={"email": "E@h3.local ", "role": "editor"})
    assert {m["email"]: m["role"] for m in r.json()["members"]} == {"o@h3.local": "owner", "e@h3.local": "editor"}
    assert [m["is_me"] for m in r.json()["members"] if m["email"] == "o@h3.local"] == [True]
    await login(client, "e@h3.local")
    assert [p["my_role"] for p in (await client.get("/api/projects")).json()["projects"]] == ["editor"]


async def test_an_unknown_email_says_so_and_a_repeat_is_409(client, db):
    pid = await _owner_with_project(client)
    r = await client.post(f"/api/projects/{pid}/members", json={"email": "nobody@h3.local", "role": "viewer"})
    assert r.status_code == 404 and "Nobody has an H3 account" in r.text
    await users.create("e@h3.local", "passphrase-1")
    body = {"email": "e@h3.local", "role": "viewer"}
    assert (await client.post(f"/api/projects/{pid}/members", json=body)).status_code == 200
    assert (await client.post(f"/api/projects/{pid}/members", json=body)).status_code == 409


async def test_the_owner_can_change_a_role_and_rename(client, db):
    pid, e_id = await _owner_and_member(client, "viewer")
    await login(client, "o@h3.local")
    r = await client.patch(f"/api/projects/{pid}/members/{e_id}", json={"role": "editor"})
    assert {m["email"]: m["role"] for m in r.json()["members"]}["e@h3.local"] == "editor"
    r = await client.patch(f"/api/projects/{pid}", json={"name": " Final cut "})
    assert r.json()["name"] == "Final cut"
    assert (await client.patch(f"/api/projects/{pid}", json={"name": ""})).status_code == 400


async def test_only_the_owner_renames_or_manages_people(client, db):
    pid, e_id = await _owner_and_member(client, "editor")
    assert (await client.patch(f"/api/projects/{pid}", json={"name": "Mine now"})).status_code == 403
    assert (await client.post(f"/api/projects/{pid}/members", json={"email": "o@h3.local", "role": "viewer"})).status_code == 403
    assert (await client.delete(f"/api/projects/{pid}")).status_code == 403


async def test_the_owner_cannot_leave_be_removed_or_demoted(client, db):
    pid = await _owner_with_project(client)
    me = (await client.get("/api/me")).json()["id"]
    assert (await client.delete(f"/api/projects/{pid}/members/{me}")).status_code == 409
    assert (await client.patch(f"/api/projects/{pid}/members/{me}", json={"role": "viewer"})).status_code == 409


async def test_a_removed_member_loses_the_project_and_its_clips(client, db):
    pid, e_id = await _owner_and_member(client, "editor")
    jid = await jobs.add(e_id, "mine", project_id=pid)
    await login(client, "o@h3.local")
    assert (await client.delete(f"/api/projects/{pid}/members/{e_id}")).status_code == 200
    await login(client, "e@h3.local")
    for path in (f"/api/projects/{pid}", f"/api/jobs/{jid}"):
        assert (await client.get(path)).status_code == 404, path


async def test_a_member_can_leave(client, db):
    pid, e_id = await _owner_and_member(client, "viewer")
    assert (await client.delete(f"/api/projects/{pid}/members/{e_id}")).status_code == 200
    assert (await client.get("/api/projects")).json()["projects"] == []


async def test_removing_someone_who_is_not_a_member_is_404(client, db):
    pid = await _owner_with_project(client)
    other = await users.create("s@h3.local", "passphrase-1")
    assert (await client.delete(f"/api/projects/{pid}/members/{other['id']}")).status_code == 404


async def test_a_busy_project_cannot_be_deleted(client, db):
    pid = await _owner_with_project(client)
    me = (await client.get("/api/me")).json()["id"]
    jid = await jobs.add(me, "rendering", project_id=pid)
    await jobs.update(jid, status="running")
    assert (await client.delete(f"/api/projects/{pid}")).status_code == 409
    assert await jobs.get_any(jid) is not None


async def test_deleting_a_project_removes_its_clips_and_their_files(client, db):
    pid = await _owner_with_project(client)
    me = (await client.get("/api/me")).json()["id"]
    jid = await jobs.add(me, "done", project_id=pid)
    store = client._transport.app.state.storage
    await store.put(f"videos/{me}/{jid}.mp4", b"mp4", "video/mp4")
    await jobs.update(jid, status="done", output_key=f"videos/{me}/{jid}.mp4", finished_at=1.0)
    assert (await client.delete(f"/api/projects/{pid}")).status_code == 200
    assert await jobs.get_any(jid) is None
    assert await store.head(f"videos/{me}/{jid}.mp4") is None


async def test_a_store_failure_keeps_the_project(client, db):
    pid = await _owner_with_project(client)
    me = (await client.get("/api/me")).json()["id"]
    jid = await jobs.add(me, "done", project_id=pid)
    await jobs.update(jid, status="done", output_key=f"videos/{me}/{jid}.mp4", finished_at=1.0)
    store = client._transport.app.state.storage

    async def boom(key):
        raise RuntimeError("store down")
    store.delete = boom
    r = await client.delete(f"/api/projects/{pid}")
    assert r.status_code == 502 and "project was kept" in r.text
    assert await jobs.get_any(jid) is not None


async def test_teammates_see_each_others_avatars_strangers_do_not(client, db):
    pid, e_id = await _owner_and_member(client, "viewer")
    await users.create("s@h3.local", "passphrase-1")
    await login(client, "o@h3.local")
    url = (await client.post("/api/me/avatar", files={"file": ("me.png", _image(), "image/png")})).json()["avatar_url"]
    await login(client, "e@h3.local")
    assert (await client.get(url)).status_code == 200
    await login(client, "s@h3.local")
    assert (await client.get(url)).status_code == 404
