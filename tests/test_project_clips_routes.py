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


async def test_again_all_skips_projects_where_i_can_only_watch(client, db):
    t = await _team(client)
    mine = await _done_clip(client, t["e"], t["pid"])
    personal = await projects.personal_for(t["e"])
    kept = await _done_clip(client, t["e"], personal)
    await login(client, "o@h3.local")
    assert (await client.patch(f"/api/projects/{t['pid']}/members/{t['e']}",
                               json={"role": "viewer"})).status_code == 200
    await login(client, "e@h3.local")
    before = len(await jobs.list_for(t["e"], 100, project_id=t["pid"]))
    r = await client.post("/api/jobs/again-all", json={})
    assert r.json() == {"queued": 1, "capped": False}
    assert len(await jobs.list_for(t["e"], 100, project_id=t["pid"])) == before
    assert len(await jobs.list_for(t["e"], 100, project_id=personal)) == 2
    assert {j["id"] for j in await jobs.list_for(t["e"], 100, project_id=t["pid"])} == {mine}
    assert kept in {j["id"] for j in await jobs.list_for(t["e"], 100, project_id=personal)}


async def test_clear_queued_in_a_project_removes_only_my_own(client, db):
    t = await _team(client)
    theirs = await jobs.add(t["o"], "theirs", project_id=t["pid"])
    mine = await jobs.add(t["e"], "mine", project_id=t["pid"])
    await login(client, "e@h3.local")
    assert (await client.post(f"/api/jobs/clear-queued?project={t['pid']}")).json()["removed"] == 1
    assert await jobs.get_any(mine) is None or (await jobs.get_any(mine))["status"] != "queued"
    assert (await jobs.get_any(theirs))["status"] == "queued"


async def _demote_e(client, t):
    await login(client, "o@h3.local")
    r = await client.patch(f"/api/projects/{t['pid']}/members/{t['e']}", json={"role": "viewer"})
    assert r.status_code == 200
    await login(client, "e@h3.local")


async def test_a_demoted_maker_cannot_requeue_through_patch(client, db):
    t = await _team(client); jid = await _done_clip(client, t["e"], t["pid"])
    await _demote_e(client, t)
    assert (await client.patch(f"/api/jobs/{jid}", json={"prompt": "again"})).status_code == 403
    row = await jobs.get_any(jid)
    assert (row["status"], row["prompt"]) == ("done", "a clip")


async def test_a_demoted_maker_cannot_retry(client, db):
    t = await _team(client)
    jid = await jobs.add(t["e"], "broke", project_id=t["pid"])
    await jobs.update(jid, status="failed")
    await _demote_e(client, t)
    assert (await client.post(f"/api/jobs/{jid}/retry")).status_code == 403
    assert (await jobs.get_any(jid))["status"] == "failed"


async def test_a_clip_sent_without_a_project_skips_my_shared_projects(client, db):
    await sign_in(client, "solo@h3.local")
    pid = (await client.post("/api/projects", json={"name": "P"})).json()["id"]
    jid = (await client.post("/api/jobs", json={"prompts": "no tab"})).json()["created"][0]
    assert (await jobs.get_any(jid))["project_id"] != pid


async def _picture_in_p(client, t) -> str:
    """o@ uploads a picture and queues an i2v clip with it in P."""
    await login(client, "o@h3.local")
    key = (await client.post("/api/upload", files={"file": ("p.png", PNG, "image/png")})).json()["key"]
    r = await client.post(f"/api/jobs?project={t['pid']}",
                          json={"prompts": "a dog", "mode": "i2v", "ref_images": [key]})
    assert r.status_code == 200, r.text
    return key


async def test_an_editor_reuses_a_teammates_picture_in_the_same_project(client, db):
    t = await _team(client); key = await _picture_in_p(client, t)
    await login(client, "e@h3.local")
    r = await client.post(f"/api/jobs?project={t['pid']}",
                          json={"prompts": "the dog again", "mode": "i2v", "ref_images": [key]})
    row = await jobs.get_any(r.json()["created"][0])
    assert (row["mode"], row["ref_images"]) == ("i2v", [key])


async def test_a_teammates_picture_cannot_be_carried_into_another_project(client, db):
    t = await _team(client); key = await _picture_in_p(client, t)
    q = (await client.post("/api/projects", json={"name": "Q"})).json()["id"]
    assert (await client.post(f"/api/projects/{q}/members",
                              json={"email": "e@h3.local", "role": "editor"})).status_code == 200
    await login(client, "e@h3.local")
    r = await client.post(f"/api/jobs?project={q}",
                          json={"prompts": "smuggled", "mode": "i2v", "ref_images": [key]})
    assert (await jobs.get_any(r.json()["created"][0]))["ref_images"] == []


async def test_a_teammates_videos_sounds_keyframes_and_voice_carry_within_the_project(client, db):
    t = await _team(client)
    o = f"uploads/{t['o']}/"
    vid, snd, kf, voice = o + "v.mp4", o + "s.mp3", o + "k.png", o + "voice.mp3"
    await jobs.add(t["o"], "refs", mode="r2v", ref_videos=[vid], ref_audios=[snd], project_id=t["pid"])
    await jobs.add(t["o"], "guided", mode="t2v", keyframes=[{"key": kf, "at": 2.0}],
                   audio_key=voice, project_id=t["pid"])
    await login(client, "e@h3.local")
    r = await client.post(f"/api/jobs?project={t['pid']}", json={
        "prompts": "refs again", "mode": "r2v", "ref_videos": [vid], "ref_audios": [snd]})
    row = await jobs.get_any(r.json()["created"][0])
    assert (row["ref_videos"], row["ref_audios"]) == ([vid], [snd])
    r = await client.post(f"/api/jobs?project={t['pid']}", json={
        "prompts": "guided again", "mode": "t2v", "seconds": 5,
        "keyframes": [{"key": kf, "at": 2.0}], "audio": voice})
    assert r.status_code == 200, r.text
    row = await jobs.get_any(r.json()["created"][0])
    assert ([k["key"] for k in row["keyframes"]], row["audio_key"]) == ([kf], voice)


async def test_editing_my_clip_may_use_a_teammates_picture_from_its_project(client, db):
    t = await _team(client); key = await _picture_in_p(client, t)
    mine = await jobs.add(t["e"], "mine", mode="t2v", project_id=t["pid"])
    elsewhere = await jobs.add(t["e"], "elsewhere", mode="t2v")      # e's own My clips
    await login(client, "e@h3.local")
    assert (await client.patch(f"/api/jobs/{mine}", json={"ref_images": [key]})).status_code == 200
    assert (await jobs.get_any(mine))["ref_images"] == [key]
    assert (await client.patch(f"/api/jobs/{elsewhere}", json={"ref_images": [key]})).status_code == 200
    assert (await jobs.get_any(elsewhere))["ref_images"] == []


async def test_a_demoted_maker_cannot_delete_cancel_or_hide_their_own_clips(client, db):
    t = await _team(client)
    done = await _done_clip(client, t["e"], t["pid"])
    queued = await jobs.add(t["e"], "waiting", project_id=t["pid"])
    key = (await jobs.get_any(done))["output_key"]
    store = client._transport.app.state.storage
    await _demote_e(client, t)
    assert (await client.delete(f"/api/archive/{done}")).status_code == 403
    assert (await client.post("/api/archive/delete", json={"ids": [done]})).json() == {"deleted": 0, "kept": 0}
    assert (await client.delete(f"/api/jobs/{done}")).status_code == 403
    assert (await client.post(f"/api/jobs/{queued}/cancel")).status_code == 403
    assert (await client.patch(f"/api/jobs/{queued}", json={"prompt": "changed"})).status_code == 403
    assert await store.head(key) is not None
    assert (await jobs.get_any(done))["status"] == "done"
    row = await jobs.get_any(queued)
    assert (row["status"], row["prompt"]) == ("queued", "waiting")
    # Still in the feed (not hidden), and the shapes say the same as the routes did.
    shown = (await client.get(f"/api/jobs?project={t['pid']}")).json()["jobs"]
    assert {j["id"]: j["can_manage"] for j in shown} == {done: False, queued: False}
