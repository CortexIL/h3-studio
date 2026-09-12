from __future__ import annotations

import asyncio
import time

from app.store import jobs, kv, runs, users


async def _two_users():
    a = await users.create("a@h3.local", "passphrase-1")
    b = await users.create("b@h3.local", "passphrase-2")
    return a, b


async def test_add_and_list_is_scoped(db):
    a, b = await _two_users()
    await jobs.add(a["id"], "a's clip")
    await jobs.add(b["id"], "b's clip")
    mine = await jobs.list_for(a["id"])
    assert [j["prompt"] for j in mine] == ["a's clip"]


async def test_get_for_refuses_another_users_job(db):
    a, b = await _two_users()
    job_id = await jobs.add(a["id"], "private")
    assert await jobs.get_for(a["id"], job_id) is not None
    assert await jobs.get_for(b["id"], job_id) is None


async def test_get_any_reaches_every_job(db):
    """The orchestrator has no user in hand; this is the door it uses."""
    a, _ = await _two_users()
    job_id = await jobs.add(a["id"], "private")
    assert (await jobs.get_any(job_id))["prompt"] == "private"


async def test_delete_is_scoped(db):
    a, b = await _two_users()
    job_id = await jobs.add(a["id"], "private")
    assert await jobs.delete_for(b["id"], job_id) is False
    assert await jobs.get_for(a["id"], job_id) is not None
    assert await jobs.delete_for(a["id"], job_id) is True


async def test_ref_images_round_trip_as_a_list(db):
    a, _ = await _two_users()
    job_id = await jobs.add(a["id"], "p", ref_images=["uploads/x/1.png"])
    assert (await jobs.get_for(a["id"], job_id))["ref_images"] == ["uploads/x/1.png"]
    await jobs.update(job_id, ref_images=["uploads/x/2.png"])
    assert (await jobs.get_for(a["id"], job_id))["ref_images"] == ["uploads/x/2.png"]


async def test_a_large_seed_survives(db):
    """ComfyUI seeds routinely exceed 32 bits; the SQLite column hid that."""
    a, _ = await _two_users()
    big = 2 ** 53 - 1
    job_id = await jobs.add(a["id"], "p", seed=big)
    assert (await jobs.get_for(a["id"], job_id))["seed"] == big


async def test_claim_hands_each_job_to_exactly_one_caller(db):
    a, _ = await _two_users()
    for i in range(20):
        await jobs.add(a["id"], f"clip {i}")
    claimed = await asyncio.gather(*[jobs.claim_next_queued() for _ in range(30)])
    ids = [c["id"] for c in claimed if c]
    assert len(ids) == 20
    assert len(set(ids)) == 20


async def test_claim_is_fifo(db):
    a, _ = await _two_users()
    first = await jobs.add(a["id"], "first")
    await jobs.add(a["id"], "second")
    assert (await jobs.claim_next_queued())["id"] == first


async def test_one_users_flood_does_not_park_another_behind_it(db):
    """The reason fair share exists: B's one clip must not wait out A's three.

    Arrival order would render a1 a2 a3 and only then b1, so a single evening of
    someone queueing forty clips locks the pod. By turn, both users' first clips
    are served before either user's second.
    """
    a, b = await _two_users()
    a1 = await jobs.add(a["id"], "a first")
    a2 = await jobs.add(a["id"], "a second")
    a3 = await jobs.add(a["id"], "a third")
    b1 = await jobs.add(b["id"], "b first")
    order = [(await jobs.claim_next_queued())["id"] for _ in range(4)]
    assert order == [a1, b1, a2, a3]


async def test_a_users_own_clips_still_run_in_the_order_they_queued_them(db):
    """Fairness is between users. Inside one user nothing about FIFO changes."""
    a, b = await _two_users()
    a1 = await jobs.add(a["id"], "a first")
    await jobs.add(b["id"], "b first")
    a2 = await jobs.add(a["id"], "a second")
    await jobs.add(b["id"], "b second")
    a3 = await jobs.add(a["id"], "a third")
    claimed = [(await jobs.claim_next_queued())["id"] for _ in range(5)]
    assert [c for c in claimed if c in {a1, a2, a3}] == [a1, a2, a3]


async def test_someone_arriving_mid_flood_waits_one_clip_not_all_of_them(db):
    """A running job still counts as its owner's backlog, which is what makes the
    turn stable while the queue drains. Were it counted only while queued, A's
    head would fall back to turn 0 after every claim, win the tie on age every
    time, and arrival order would quietly return."""
    a, b = await _two_users()
    a1 = await jobs.add(a["id"], "a first")
    a2 = await jobs.add(a["id"], "a second")
    a3 = await jobs.add(a["id"], "a third")
    assert (await jobs.claim_next_queued())["id"] == a1
    late = await jobs.add(b["id"], "b arrives after a's flood")
    assert (await jobs.claim_next_queued())["id"] == late
    assert [(await jobs.claim_next_queued())["id"] for _ in range(2)] == [a2, a3]


async def test_a_finished_clip_is_not_a_debt_against_its_owner(db):
    """Only pending work counts. Otherwise yesterday's batch would park a user
    behind everyone else today, which is the opposite unfairness."""
    a, b = await _two_users()
    for i in range(3):
        done = await jobs.add(a["id"], f"a yesterday {i}")
        await jobs.claim_next_queued()
        await jobs.update(done, status="done", output_key="k")
    a_today = await jobs.add(a["id"], "a today")
    await jobs.add(b["id"], "b today")
    assert (await jobs.claim_next_queued())["id"] == a_today


async def test_queue_positions_agree_with_the_order_jobs_are_claimed_in(db):
    """The claim orders with a correlated count, the positions with window
    functions. They are two spellings of one rule, and the only thing holding them
    together is that the position a user is shown is the one they are served from.
    """
    a, b = await _two_users()
    for i in range(3):
        await jobs.add(a["id"], f"a {i}")
    for i in range(2):
        await jobs.add(b["id"], f"b {i}")
    positions = {**await jobs.queue_positions_for(a["id"]),
                 **await jobs.queue_positions_for(b["id"])}
    by_position = sorted(positions, key=positions.get)
    claimed = [(await jobs.claim_next_queued())["id"] for _ in range(5)]
    assert by_position == claimed


async def test_counts_are_scoped(db):
    a, b = await _two_users()
    await jobs.add(a["id"], "one")
    await jobs.add(a["id"], "two")
    await jobs.add(b["id"], "three")
    assert (await jobs.counts_for(a["id"]))["queued"] == 2
    assert (await jobs.counts_all())["queued"] == 3


async def test_queue_position_counts_only_what_is_ahead(db):
    a, _ = await _two_users()
    await jobs.add(a["id"], "first")
    mine = await jobs.add(a["id"], "second")
    assert await jobs.queue_position(mine) == 1


async def test_archive_pages_forward_without_repeating(db):
    a, _ = await _two_users()
    for i in range(5):
        jid = await jobs.add(a["id"], f"clip {i}")
        await jobs.update(jid, status="done", output_key=f"videos/{a['id']}/{jid}.mp4",
                          finished_at=time.time() + i)
    page1, cur = await jobs.archive_page(a["id"], None, limit=2)
    page2, cur2 = await jobs.archive_page(a["id"], cur, limit=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {j["id"] for j in page1}.isdisjoint({j["id"] for j in page2})
    assert cur2 is not None
    page3, cur3 = await jobs.archive_page(a["id"], cur2, limit=2)
    assert len(page3) == 1 and cur3 is None


async def test_archive_excludes_other_users(db):
    a, b = await _two_users()
    jid = await jobs.add(b["id"], "b's clip")
    await jobs.update(jid, status="done", output_key="k", finished_at=1.0)
    page, _ = await jobs.archive_page(a["id"], None)
    assert page == []


async def test_archive_ignores_a_corrupt_cursor(db):
    a, _ = await _two_users()
    jid = await jobs.add(a["id"], "clip")
    await jobs.update(jid, status="done", output_key="k", finished_at=1.0)
    page, _ = await jobs.archive_page(a["id"], "not-a-cursor")
    assert len(page) == 1


async def test_requeue_stuck_running(db):
    a, _ = await _two_users()
    jid = await jobs.add(a["id"], "orphan")
    await jobs.update(jid, status="running", remote_id="r1")
    assert await jobs.requeue_stuck_running() == 1
    row = await jobs.get_for(a["id"], jid)
    assert row["status"] == "queued" and row["remote_id"] is None


async def test_clear_finished_is_scoped(db):
    a, b = await _two_users()
    mine = await jobs.add(a["id"], "mine")
    theirs = await jobs.add(b["id"], "theirs")
    for jid in (mine, theirs):
        await jobs.update(jid, status="done", output_key="k")
    assert await jobs.clear_finished_for(a["id"]) == 1
    assert await jobs.get_for(b["id"], theirs) is not None


async def test_list_all_carries_the_owner_email(db):
    a, _ = await _two_users()
    await jobs.add(a["id"], "mine")
    rows = await jobs.list_all()
    assert rows[0]["user_email"] == "a@h3.local"


async def test_kv_round_trip(db):
    assert await kv.get("policy") is None
    await kv.set("policy", "auto")
    await kv.set("policy", "off")
    assert await kv.get("policy") == "off"


async def test_runs_record_a_session(db):
    rid = await runs.start("pod-1", "RTX 5090", note="test")
    await runs.update(rid, status="stopped", cost_estimate=1.25)
    rows = await runs.recent()
    assert rows[0]["id"] == rid and rows[0]["cost_estimate"] == 1.25


# ---- B3: finished jobs leave the feed without leaving the archive ----

async def test_dismissed_jobs_leave_the_feed_but_not_the_archive(db):
    a, _ = await _two_users()
    jid = await jobs.add(a["id"], "keep me")
    await jobs.update(jid, status="done", output_key="k", finished_at=time.time())
    assert await jobs.dismiss_for(a["id"], jid) is True
    assert await jobs.list_for(a["id"]) == []
    page, _ = await jobs.archive_page(a["id"], None)
    assert [c["id"] for c in page] == [jid]


async def test_dismiss_is_scoped(db):
    a, b = await _two_users()
    jid = await jobs.add(a["id"], "p")
    assert await jobs.dismiss_for(b["id"], jid) is False


async def test_clear_finished_hides_done_and_deletes_failed_and_cancelled(db):
    a, _ = await _two_users()
    done = await jobs.add(a["id"], "done")
    await jobs.update(done, status="done", output_key="k", finished_at=1.0)
    failed = await jobs.add(a["id"], "failed")
    await jobs.update(failed, status="failed")
    cancelled = await jobs.add(a["id"], "cancelled")
    await jobs.update(cancelled, status="cancelled")
    queued = await jobs.add(a["id"], "queued")
    assert await jobs.clear_finished_for(a["id"]) == 3
    assert [j["id"] for j in await jobs.list_for(a["id"])] == [queued]
    assert await jobs.get_for(a["id"], done) is not None      # hidden, still archived
    assert await jobs.get_for(a["id"], failed) is None
    assert await jobs.get_for(a["id"], cancelled) is None


# ---- B10: state-guarded updates ----

async def test_update_if_only_changes_a_row_in_the_expected_state(db):
    a, _ = await _two_users()
    jid = await jobs.add(a["id"], "p")
    assert await jobs.update_if(jid, "running", status="done") is False
    assert (await jobs.get_any(jid))["status"] == "queued"
    assert await jobs.update_if(jid, "queued", status="cancelled") is True
    assert (await jobs.get_any(jid))["status"] == "cancelled"


async def test_queue_positions_are_per_job_and_only_mine(db):
    a, b = await _two_users()
    await jobs.add(b["id"], "b first")
    a1 = await jobs.add(a["id"], "a second")
    await jobs.add(b["id"], "b third")
    a2 = await jobs.add(a["id"], "a fourth")
    assert await jobs.queue_positions_for(a["id"]) == {a1: 1, a2: 3}


# ---- B5: archive search and filters ----

async def _done(uid, prompt, preset="final", ts=1.0):
    jid = await jobs.add(uid, prompt, preset=preset)
    await jobs.update(jid, status="done", output_key=f"k-{jid}", finished_at=ts)
    return jid


async def test_archive_search_is_case_insensitive(db):
    a, _ = await _two_users()
    red = await _done(a["id"], "A RED car")
    await _done(a["id"], "a blue boat")
    page, _ = await jobs.archive_page(a["id"], None, q="red")
    assert [c["id"] for c in page] == [red]


async def test_archive_search_treats_wildcards_literally(db):
    a, _ = await _two_users()
    pct = await _done(a["id"], "100% red")
    await _done(a["id"], "plain")
    page, _ = await jobs.archive_page(a["id"], None, q="%")
    assert [c["id"] for c in page] == [pct]
    page, _ = await jobs.archive_page(a["id"], None, q="_")
    assert page == []


async def test_archive_filters_by_preset_and_mode(db):
    a, _ = await _two_users()
    turbo = await _done(a["id"], "fast", preset="turbo")
    await _done(a["id"], "slow", preset="final")
    page, _ = await jobs.archive_page(a["id"], None, preset="turbo")
    assert [c["id"] for c in page] == [turbo]


async def test_archive_pagination_stays_inside_the_filter(db):
    a, _ = await _two_users()
    wanted = [await _done(a["id"], f"red {i}", ts=float(i)) for i in range(5)]
    for i in range(5):
        await _done(a["id"], f"blue {i}", ts=float(i) + 0.5)
    seen, cursor = [], None
    while True:
        page, cursor = await jobs.archive_page(a["id"], cursor, limit=2, q="red")
        seen += [c["id"] for c in page]
        if not cursor:
            break
    assert sorted(seen) == sorted(wanted)


async def test_archive_search_never_matches_another_users_clip(db):
    a, b = await _two_users()
    await _done(b["id"], "their red car")
    page, _ = await jobs.archive_page(a["id"], None, q="red")
    assert page == []


# ---- B9: per-user usage for the admin page ----

async def test_usage_by_user_counts_and_bytes(db):
    a, b = await _two_users()
    for size in (100, 50):
        jid = await jobs.add(a["id"], "done")
        await jobs.update(jid, status="done", output_key="k", output_bytes=size, finished_at=1.0)
    await jobs.add(a["id"], "waiting")
    usage = await jobs.usage_by_user()
    assert usage[a["id"]]["done"] == 2 and usage[a["id"]]["queued"] == 1
    assert usage[a["id"]]["stored_bytes"] == 150
    assert b["id"] not in usage
