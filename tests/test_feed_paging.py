"""The feed is a page of the queue, and says so.

A capped list with no total beside it reads as a total. That is how a queue of
two hundred and sixty clips could describe itself as exactly two hundred, with
the sixty it left out being the ones about to render.
"""
from __future__ import annotations

from app.store import jobs, users
from tests.conftest import sign_in


async def test_the_counts_are_the_whole_feed_even_when_the_page_is_not(client, db):
    u = await sign_in(client)
    for i in range(5):
        await jobs.add(u["id"], f"clip {i}")
    body = (await client.get("/api/jobs", params={"limit": 2})).json()
    assert len(body["jobs"]) == 2
    assert body["counts"]["all"] == 5
    assert body["counts"]["active"] == 5
    assert body["has_more"] is True


async def test_the_counts_speak_the_studio_s_own_four_words(client, db):
    u = await sign_in(client)
    await jobs.add(u["id"], "waiting")
    running = await jobs.add(u["id"], "on the gpu")
    done = await jobs.add(u["id"], "finished")
    failed = await jobs.add(u["id"], "broke")
    cancelled = await jobs.add(u["id"], "stopped")
    await jobs.update(running, status="running")
    await jobs.update(done, status="done", finished_at=1.0)
    await jobs.update(failed, status="failed", error="no")
    await jobs.update(cancelled, status="cancelled")
    counts = (await client.get("/api/jobs")).json()["counts"]
    assert counts == {"all": 5, "active": 2, "ready": 1, "failed": 2}


async def test_the_counts_are_mine_alone(client, db):
    other = await users.create("b@h3.local", "passphrase-2")
    for i in range(3):
        await jobs.add(other["id"], f"not mine {i}")
    u = await sign_in(client)
    await jobs.add(u["id"], "mine")
    assert (await client.get("/api/jobs")).json()["counts"]["all"] == 1


async def test_a_capped_page_keeps_the_clip_that_is_next_to_render(client, db):
    """What falls off a full page is the far end of the queue, never its head."""
    u = await sign_in(client)
    first = await jobs.add(u["id"], "next up")
    for i in range(4):
        await jobs.add(u["id"], f"behind it {i}")
    finished = await jobs.add(u["id"], "done ages ago")
    await jobs.update(finished, status="done", finished_at=1.0)

    body = (await client.get("/api/jobs", params={"limit": 2})).json()
    shown = [j["id"] for j in body["jobs"]]
    assert first in shown
    assert finished not in shown


async def test_a_dragged_queue_keeps_its_own_order_under_the_cap(client, db):
    u = await sign_in(client)
    ids = [await jobs.add(u["id"], f"clip {i}") for i in range(4)]
    # Render the last one first.
    await jobs.reorder_for(u["id"], [ids[3], ids[0], ids[1], ids[2]])
    body = (await client.get("/api/jobs", params={"limit": 2})).json()
    assert ids[3] in [j["id"] for j in body["jobs"]]


async def test_the_page_size_is_bounded_on_both_sides(client, db):
    await sign_in(client)
    assert (await client.get("/api/jobs", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/jobs", params={"limit": 100_000})).status_code == 422
    assert (await client.get("/api/jobs", params={"limit": 1})).status_code == 200


async def test_run_all_again_reaches_past_the_first_page(client, db):
    """It used to filter the feed page, which meant "all" was "the first 200"."""
    u = await sign_in(client)
    done = []
    for i in range(3):
        jid = await jobs.add(u["id"], f"finished {i}")
        await jobs.update(jid, status="done", finished_at=float(i))
        done.append(jid)
    # Enough unfinished work that a small page would be nothing but queue.
    for i in range(5):
        await jobs.add(u["id"], f"waiting {i}")

    r = await client.post("/api/jobs/again-all", json={"status": "done"})
    assert r.status_code == 200
    assert r.json() == {"queued": 3, "capped": False}


async def test_a_status_the_store_is_asked_for_is_the_status_it_returns(client, db):
    u = await sign_in(client)
    queued = await jobs.add(u["id"], "waiting")
    finished = await jobs.add(u["id"], "finished")
    await jobs.update(finished, status="done", finished_at=1.0)
    rows = await jobs.list_for(u["id"], statuses=("done",))
    assert [r["id"] for r in rows] == [finished]
    assert queued not in [r["id"] for r in rows]
    assert await jobs.list_for(u["id"], statuses=()) == []
