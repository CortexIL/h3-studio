from __future__ import annotations

from app.store import jobs, users
from tests.conftest import sign_in


async def test_creating_a_job_attaches_the_signed_in_user(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "a red car"})
    assert r.status_code == 200 and r.json()["count"] == 1
    rows = await jobs.list_for(u["id"])
    assert rows[0]["prompt"] == "a red car"


async def test_the_feed_shows_only_my_jobs(client, db):
    other = await users.create("b@h3.local", "passphrase-2")
    await jobs.add(other["id"], "not mine")
    await sign_in(client)
    await client.post("/api/jobs", json={"prompts": "mine"})
    listed = (await client.get("/api/jobs")).json()["jobs"]
    assert [j["prompt"] for j in listed] == ["mine"]


async def test_lines_split_makes_one_job_per_line(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs",
                          json={"prompts": "one\ntwo\nthree", "split": "lines"})
    assert r.json()["count"] == 3


async def test_a_paragraph_is_one_job_by_default(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "one\ntwo\nthree"})
    assert r.json()["count"] == 1


async def test_takes_get_their_own_seeds(client, db):
    u = await sign_in(client)
    await client.post("/api/jobs", json={"prompts": "p", "count": 3, "seed": 42})
    seeds = [j["seed"] for j in await jobs.list_for(u["id"])]
    assert seeds == [None, None, None]   # 3 takes on one seed would be identical


async def test_a_single_take_keeps_the_pinned_seed(client, db):
    u = await sign_in(client)
    await client.post("/api/jobs", json={"prompts": "p", "count": 1, "seed": 42})
    assert (await jobs.list_for(u["id"]))[0]["seed"] == 42


async def test_unknown_preset_is_refused(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "preset": "ultra"})
    assert r.status_code == 400


async def test_unknown_mode_is_refused(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "mode": "x2v"})
    assert r.status_code == 400


async def test_empty_prompt_is_refused(client, db):
    await sign_in(client)
    assert (await client.post("/api/jobs",
                              json={"prompts": "   "})).status_code == 400


async def test_editing_a_running_job_is_refused(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="running")
    r = await client.patch(f"/api/jobs/{jid}", json={"prompt": "new"})
    assert r.status_code == 409


async def test_editing_a_finished_job_requeues_it(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="done", output_key="k")
    r = await client.patch(f"/api/jobs/{jid}", json={"prompt": "revised"})
    assert r.status_code == 200 and r.json()["requeued"] is True
    row = await jobs.get_for(u["id"], jid)
    assert row["status"] == "queued" and row["prompt"] == "revised"


async def test_again_clones_rather_than_resetting(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="done", output_key="k")
    r = await client.post(f"/api/jobs/{jid}/again")
    assert r.status_code == 200
    assert len(await jobs.list_for(u["id"])) == 2
    assert (await jobs.get_for(u["id"], jid))["status"] == "done"   # original kept


async def test_delete_removes_the_row(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    assert (await client.delete(f"/api/jobs/{jid}")).status_code == 200
    assert await jobs.get_for(u["id"], jid) is None


async def test_deleting_a_running_job_is_refused(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="running")
    assert (await client.delete(f"/api/jobs/{jid}")).status_code == 409


async def test_status_reports_my_counts_and_a_queue_depth(client, db):
    other = await users.create("b@h3.local", "passphrase-2")
    await jobs.add(other["id"], "theirs")
    await sign_in(client)
    await client.post("/api/jobs", json={"prompts": "mine"})
    body = (await client.get("/api/status")).json()
    assert body["counts"]["queued"] == 1
    assert body["queue"]["total_queued"] == 2
    assert "theirs" not in str(body)


async def test_status_never_carries_the_runpod_key(client, db):
    await sign_in(client)
    body = (await client.get("/api/status")).text
    assert "api_key" not in body


async def test_archive_lists_only_my_finished_clips(client, db):
    u = await sign_in(client)
    other = await users.create("b@h3.local", "passphrase-2")
    theirs = await jobs.add(other["id"], "theirs")
    await jobs.update(theirs, status="done", output_key="k", finished_at=1.0)
    mine = await jobs.add(u["id"], "mine")
    await jobs.update(mine, status="done", output_key="k2", finished_at=2.0)
    body = (await client.get("/api/archive")).json()
    assert [c["id"] for c in body["clips"]] == [mine]
    assert "k2" not in str(body)          # a URL, never the object key


async def test_estimate_needs_no_gpu(client, db):
    await sign_in(client)
    r = await client.post("/api/estimate",
                          json={"prompts": "a\nb", "split": "lines"})
    assert r.status_code == 200 and r.json()["clips"] == 2


async def test_login_then_me(client, db):
    await sign_in(client, "someone@h3.local")
    assert (await client.get("/api/me")).json()["email"] == "someone@h3.local"


async def test_unauthenticated_request_is_401(client, db):
    assert (await client.get("/api/me")).status_code == 401


async def test_logout_clears_the_session(client, db):
    await sign_in(client)
    await client.post("/api/auth/logout")
    assert (await client.get("/api/me")).status_code == 401


async def test_disabling_a_user_kills_their_live_session(client, db):
    u = await sign_in(client)
    assert (await client.get("/api/me")).status_code == 200
    await users.set_active(u["id"], False)
    assert (await client.get("/api/me")).status_code == 401


async def test_login_failure_is_indistinguishable(client, db):
    await users.create("a@h3.local", "passphrase-1")
    wrong_pw = await client.post("/api/auth/login",
                                 json={"email": "a@h3.local", "password": "nope"})
    no_user = await client.post("/api/auth/login",
                                json={"email": "zz@h3.local",
                                      "password": "passphrase-1"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["detail"] == no_user.json()["detail"]


async def test_health_needs_no_session(client, db):
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
