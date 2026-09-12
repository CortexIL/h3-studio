"""Dragging your own queue around, without touching anyone else's turn.

Fair share (005) asks how many of an owner's pending jobs sit before this one.
A reorder rewrites that answer *within* one owner's backlog by permuting the
positions its jobs already hold, so the count per owner is unchanged and the
pod is still shared the same way between people.
"""
from __future__ import annotations

from app.store import jobs, users


async def _queue(user_id: str, n: int) -> list[str]:
    return [await jobs.add(user_id, f"clip {i}") for i in range(n)]


async def _claim_all(n: int) -> list[str]:
    claimed = []
    for _ in range(n):
        row = await jobs.claim_next_queued()
        if row is None:
            break
        claimed.append(row["id"])
    return claimed


async def test_reordering_changes_which_clip_renders_next(db):
    u = await users.create("a@h3.local", "passphrase-1")
    first, second, third = await _queue(u["id"], 3)

    moved = await jobs.reorder_for(u["id"], [third, first, second])

    assert moved == 3
    assert await _claim_all(3) == [third, first, second]


async def test_another_persons_clip_cannot_be_moved(db):
    mine = await users.create("a@h3.local", "passphrase-1")
    theirs = await users.create("b@h3.local", "passphrase-1")
    await _queue(mine["id"], 2)
    their_first, their_second = await _queue(theirs["id"], 2)

    moved = await jobs.reorder_for(mine["id"], [their_second, their_first])

    assert moved == 0, "ids that are not yours are skipped, not an error"
    positions = await jobs.queue_positions_for(theirs["id"])
    assert positions[their_first] < positions[their_second]


async def test_a_clip_that_left_the_queue_mid_drag_is_skipped(db):
    u = await users.create("a@h3.local", "passphrase-1")
    first, second = await _queue(u["id"], 2)
    running = await jobs.claim_next_queued()
    assert running["id"] == first

    moved = await jobs.reorder_for(u["id"], [first, second])

    assert moved == 1, "only the still-queued clip counts"


async def test_positions_still_agree_with_the_order_clips_are_claimed_in(db):
    """The claim and the position query are two spellings of one rule."""
    a = await users.create("a@h3.local", "passphrase-1")
    b = await users.create("b@h3.local", "passphrase-1")
    a_jobs = await _queue(a["id"], 3)
    b_jobs = await _queue(b["id"], 2)
    await jobs.reorder_for(a["id"], [a_jobs[2], a_jobs[0], a_jobs[1]])

    shown = {**await jobs.queue_positions_for(a["id"]),
             **await jobs.queue_positions_for(b["id"])}
    expected = [jid for jid, _ in sorted(shown.items(), key=lambda kv: kv[1])]

    assert await _claim_all(5) == expected


async def test_reordering_your_own_clips_does_not_jump_the_shared_queue(db):
    """A permutation keeps each owner's backlog the same size, so turns hold."""
    a = await users.create("a@h3.local", "passphrase-1")
    b = await users.create("b@h3.local", "passphrase-1")
    a_jobs = await _queue(a["id"], 3)
    b_only = (await _queue(b["id"], 1))[0]

    await jobs.reorder_for(a["id"], [a_jobs[2], a_jobs[1], a_jobs[0]])

    claimed = await _claim_all(4)
    assert claimed[0] == a_jobs[2], "their own order changed"
    assert claimed[1] == b_only, "the other user still goes second"
