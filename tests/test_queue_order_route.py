"""Reordering over the API: your own queue, and nobody else's."""
from __future__ import annotations

from app.store import jobs as jobs_store
from tests.conftest import sign_in


async def _queued_ids(client) -> list[str]:
    """The caller's queued jobs, in the order they will render."""
    body = (await client.get("/api/jobs")).json()
    queued = [j for j in body["jobs"] if j["status"] == "queued"]
    return [j["id"] for j in sorted(queued, key=lambda j: j["queue_position"])]


async def test_a_drag_changes_the_order_your_clips_render_in(client, db):
    u = await sign_in(client)
    first, second, third = [await jobs_store.add(u["id"], f"clip {i}") for i in range(3)]

    r = await client.post("/api/jobs/order", json={"ids": [third, first, second]})

    assert r.status_code == 200
    assert r.json() == {"ok": True, "reordered": 3}
    assert await _queued_ids(client) == [third, first, second]


async def test_another_persons_clips_are_skipped_not_refused(client, db):
    owner = await sign_in(client, "owner@h3.local")
    theirs = [await jobs_store.add(owner["id"], f"theirs {i}") for i in range(2)]
    await client.post("/api/auth/logout")

    await sign_in(client, "other@h3.local")
    r = await client.post("/api/jobs/order", json={"ids": list(reversed(theirs))})

    assert r.status_code == 200
    assert r.json()["reordered"] == 0

    await client.post("/api/auth/logout")
    # Sign back in rather than sign_in(), which creates the account it logs into.
    await client.post("/api/auth/login",
                      json={"email": "owner@h3.local", "password": "passphrase-1"})
    assert await _queued_ids(client) == theirs, "their queue is untouched"


async def test_an_empty_list_is_a_bad_request(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs/order", json={"ids": []})
    assert r.status_code == 400
