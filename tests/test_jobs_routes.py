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


# ---- B3: removing and clearing never lose an archived clip ----

async def test_removing_a_finished_job_keeps_it_in_the_archive(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "keep")
    await jobs.update(jid, status="done", output_key="k", finished_at=1.0)
    r = await client.delete(f"/api/jobs/{jid}")
    assert r.status_code == 200 and r.json()["hidden"] is True
    assert (await client.get("/api/jobs")).json()["jobs"] == []
    clips = (await client.get("/api/archive")).json()["clips"]
    assert [c["id"] for c in clips] == [jid]


async def test_removing_an_unfinished_job_deletes_it(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "never ran")
    r = await client.delete(f"/api/jobs/{jid}")
    assert r.status_code == 200 and r.json()["hidden"] is False
    assert await jobs.get_for(u["id"], jid) is None


async def test_clear_finished_keeps_archive_clips(client, db):
    u = await sign_in(client)
    done = await jobs.add(u["id"], "keep")
    await jobs.update(done, status="done", output_key="k", finished_at=1.0)
    failed = await jobs.add(u["id"], "broken")
    await jobs.update(failed, status="failed")
    assert (await client.post("/api/jobs/clear-finished")).json()["removed"] == 2
    clips = (await client.get("/api/archive")).json()["clips"]
    assert [c["id"] for c in clips] == [done]


# ---- B10: cancel and retry respect the job's state ----

async def test_cancelling_a_finished_job_is_refused(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="done", output_key="k", finished_at=1.0)
    assert (await client.post(f"/api/jobs/{jid}/cancel")).status_code == 409
    assert (await jobs.get_for(u["id"], jid))["status"] == "done"


async def test_cancelling_a_queued_job_marks_it_finished(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    assert (await client.post(f"/api/jobs/{jid}/cancel")).status_code == 200
    row = await jobs.get_for(u["id"], jid)
    assert row["status"] == "cancelled" and row["finished_at"] is not None


async def test_retry_is_refused_while_running(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="running")
    assert (await client.post(f"/api/jobs/{jid}/retry")).status_code == 409


async def test_retry_is_refused_for_a_finished_job(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="done", output_key="k", finished_at=1.0)
    assert (await client.post(f"/api/jobs/{jid}/retry")).status_code == 409
    assert (await jobs.get_for(u["id"], jid))["output_key"] == "k"


async def test_retry_resets_a_failed_job(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="failed", attempts=3, error="boom", finished_at=1.0)
    assert (await client.post(f"/api/jobs/{jid}/retry")).status_code == 200
    row = await jobs.get_for(u["id"], jid)
    assert (row["status"], row["attempts"], row["error"], row["finished_at"]) == \
        ("queued", 0, None, None)


# ---- B11: users never see admin notices ----

async def test_a_policy_change_is_not_announced_to_users(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    assert (await client.post("/api/admin/policy", json={"policy": "off"})).status_code == 200
    await client.post("/api/auth/logout")
    await sign_in(client, "plain@h3.local")
    assert "policy" not in (await client.get("/api/status")).text.lower()


# ---- B1: the job payload carries URLs, never storage keys or internal ids ----

async def test_jobs_never_expose_storage_keys_or_internal_ids(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="done", output_key="videos/x/secret.mp4",
                      remote_id="r1", finished_at=1.0)
    listed = (await client.get("/api/jobs")).json()["jobs"][0]
    single = (await client.get(f"/api/jobs/{jid}")).json()
    for body in (listed, single):
        assert not {"output_key", "remote_id", "user_id"} & set(body)
        assert body["video_url"] == f"/api/video/{jid}"
        assert "secret.mp4" not in str(body)


async def test_an_unfinished_job_has_no_video_url(client, db):
    u = await sign_in(client)
    await jobs.add(u["id"], "p")
    assert (await client.get("/api/jobs")).json()["jobs"][0]["video_url"] is None


async def test_an_edited_job_comes_back_in_the_public_shape(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    body = (await client.patch(f"/api/jobs/{jid}", json={"prompt": "q"})).json()["job"]
    assert "output_key" not in body and body["prompt"] == "q"


# ---- B2: queued jobs say how many are ahead, never whose ----

async def test_queued_jobs_carry_their_queue_position(client, db):
    other = await users.create("b@h3.local", "passphrase-2")
    await jobs.add(other["id"], "theirs, queued first")
    await sign_in(client)
    await client.post("/api/jobs", json={"prompts": "mine"})
    job = (await client.get("/api/jobs")).json()["jobs"][0]
    assert job["queue_position"] == 1


async def test_only_queued_jobs_have_a_queue_position(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="running")
    assert (await client.get("/api/jobs")).json()["jobs"][0]["queue_position"] is None


# ---- start to end: the frames are positional, so the count has to be exact ----

def _key(u, name: str) -> str:
    return f"uploads/{u['id']}/{name}"


async def test_start_to_end_keeps_both_frames_in_the_order_they_were_sent(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "p", "mode": "flf2v",
        "ref_images": [_key(u, "start.png"), _key(u, "end.png")]})
    assert r.status_code == 200, r.text
    assert (await jobs.list_for(u["id"]))[0]["ref_images"] == [
        _key(u, "start.png"), _key(u, "end.png")]


async def test_start_to_end_refuses_a_single_frame(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "p", "mode": "flf2v", "ref_images": [_key(u, "start.png")]})
    assert r.status_code == 400
    assert "end frame" in r.json()["detail"]


async def test_start_to_end_refuses_more_frames_than_it_can_use(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "p", "mode": "flf2v",
        "ref_images": [_key(u, f"{i}.png") for i in range(3)]})
    assert r.status_code == 400


async def test_another_persons_frame_is_refused_not_quietly_promoted(client, db):
    """The filter drops a key that is not mine rather than refusing it, so without
    counting afterwards the survivor would slide into the start frame's place and
    the clip would render from the wrong picture."""
    other = await users.create("b@h3.local", "passphrase-2")
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "p", "mode": "flf2v",
        "ref_images": [f"uploads/{other['id']}/theirs.png", _key(u, "mine.png")]})
    assert r.status_code == 400
    assert await jobs.list_for(u["id"]) == []


async def test_switching_an_existing_job_to_start_to_end_needs_both_frames(client, db):
    """The patch is judged on the row as it would end up, not on what it carries."""
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p", ref_images=[_key(u, "only.png")])
    assert (await client.patch(f"/api/jobs/{jid}", json={"mode": "flf2v"})).status_code == 400


# ---- re-running a selection ----

async def test_a_selection_queues_a_fresh_take_of_each_clip(client, db):
    u = await sign_in(client)
    first = await jobs.add(u["id"], "a red car", seconds=6, preset="turbo")
    second = await jobs.add(u["id"], "a lighthouse", seconds=9)
    for jid in (first, second):
        await jobs.update(jid, status="done")

    r = await client.post("/api/jobs/again", json={"ids": [first, second]})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 2

    fresh = [j for j in await jobs.list_for(u["id"]) if j["status"] == "queued"]
    assert sorted(j["prompt"] for j in fresh) == ["a lighthouse", "a red car"]
    # the settings come with it, the seed deliberately does not
    car = next(j for j in fresh if j["prompt"] == "a red car")
    assert car["seconds"] == 6 and car["preset"] == "turbo" and car["seed"] is None


async def test_the_sound_choice_comes_with_a_re_run(client, db):
    """Otherwise a silent take comes back with noise on it."""
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p", keep_audio=False)
    await jobs.update(jid, status="done")
    await client.post("/api/jobs/again", json={"ids": [jid]})
    assert [j["keep_audio"] for j in await jobs.list_for(u["id"])] == [False, False]


async def test_another_persons_clip_in_the_selection_is_skipped(client, db):
    other = await users.create("b@h3.local", "passphrase-2")
    theirs = await jobs.add(other["id"], "not yours")
    u = await sign_in(client)
    mine = await jobs.add(u["id"], "mine")
    await jobs.update(mine, status="done")

    r = await client.post("/api/jobs/again", json={"ids": [theirs, mine]})
    assert r.json()["queued"] == 1
    assert [j["prompt"] for j in await jobs.list_for(u["id"]) if j["status"] == "queued"] == ["mine"]
    assert len(await jobs.list_for(other["id"])) == 1


async def test_re_running_only_other_peoples_clips_is_a_404(client, db):
    other = await users.create("c@h3.local", "passphrase-2")
    theirs = await jobs.add(other["id"], "not yours")
    await sign_in(client)
    assert (await client.post("/api/jobs/again", json={"ids": [theirs]})).status_code == 404


async def test_re_running_nothing_is_refused(client, db):
    await sign_in(client)
    assert (await client.post("/api/jobs/again", json={"ids": []})).status_code == 400


async def test_the_same_clip_twice_is_queued_once(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p")
    await jobs.update(jid, status="done")
    r = await client.post("/api/jobs/again", json={"ids": [jid, jid]})
    assert r.json()["queued"] == 1
