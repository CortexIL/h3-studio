from __future__ import annotations

from app.config import Config
from app.orchestrator import Orchestrator
from app.store import jobs, users
from tests.fakes import FakeBackend, FakeSink, FakeStorage


def _cfg(app_settings) -> Config:
    return Config.from_settings(app_settings)


async def _orch(app_settings, backend=None, sink=None, storage=None):
    o = Orchestrator(_cfg(app_settings), backend or FakeBackend(),
                     sink or FakeSink(), storage or FakeStorage())
    await o.start()
    return o


async def _drain(o, job_id, ticks=10):
    for _ in range(ticks):
        await o._tick()
        row = await jobs.get_any(job_id)
        if row["status"] in {"done", "failed"}:
            return row
    return await jobs.get_any(job_id)


async def test_becomes_leader_and_drains_the_queue(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "a clip")
    backend, sink = FakeBackend(), FakeSink()
    o = await _orch(app_settings, backend, sink)
    try:
        assert o.leader is True
        await o.set_policy("auto")
        row = await _drain(o, jid)
        assert row["status"] == "done"
        assert row["output_key"] == f"videos/{u['id']}/{jid}.mp4"
        assert sink.saved and sink.saved[0][1] == b"mp4-bytes"
    finally:
        await o.stop()


async def test_a_second_orchestrator_is_not_leader(db, app_settings):
    first = await _orch(app_settings)
    try:
        second = Orchestrator(_cfg(app_settings), FakeBackend(), FakeSink(),
                              FakeStorage())
        await second.start()
        assert second.leader is False
        await second.stop()
        # A follower must never bring a pod up.
        assert second.backend.up is False
    finally:
        await first.stop()


async def test_policy_off_tears_the_pod_down(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    await jobs.add(u["id"], "a clip")
    backend = FakeBackend()
    o = await _orch(app_settings, backend)
    try:
        await o.set_policy("auto")
        await o._tick()
        assert backend.up is True
        await o.set_policy("off")
        await o._tick()
        assert backend.up is False
    finally:
        await o.stop()


async def test_a_job_that_keeps_failing_stops_after_three_attempts(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "doomed")
    o = await _orch(app_settings, FakeBackend(fail_times=99))
    try:
        await o.set_policy("auto")
        row = await _drain(o, jid, ticks=8)
        assert row["status"] == "failed"
        assert row["attempts"] == 3
    finally:
        await o.stop()


async def test_a_sink_failure_retries_rather_than_losing_the_clip(db, app_settings):
    """A store that is briefly down must not silently drop a paid-for clip.

    The job goes back on the queue and is picked up again inside the same tick,
    so the observable evidence of the retry is the attempt count reaching the cap
    before it is finally marked failed - with the store's own error kept.
    """
    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "paid for")
    o = await _orch(app_settings, FakeBackend(), FakeSink(explode=True))
    try:
        await o.set_policy("auto")
        row = await _drain(o, jid, ticks=8)
        assert row["attempts"] == 3
        assert row["status"] == "failed"
        assert "object store is down" in row["error"]
    finally:
        await o.stop()


async def test_reference_images_are_fetched_from_storage(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    key = f"uploads/{u['id']}/ref.png"
    await jobs.add(u["id"], "with a ref", ref_images=[key])
    backend = FakeBackend()
    o = await _orch(app_settings, backend, storage=FakeStorage({key: b"PNGDATA"}))
    try:
        await o.set_policy("auto")
        await o._tick()
        assert backend.uploaded == [(b"PNGDATA", "ref.png")]
    finally:
        await o.stop()


async def test_budget_ceiling_forces_policy_off(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    await jobs.add(u["id"], "expensive")
    backend = FakeBackend()
    backend.cost = 999.0
    o = await _orch(app_settings, backend)
    try:
        await o.set_policy("auto")
        await o._tick()
        assert await o.policy() == "off"
        assert backend.up is False
    finally:
        await o.stop()


async def test_snapshot_for_a_user_hides_other_users(db, app_settings):
    a = await users.create("a@h3.local", "passphrase-1")
    b = await users.create("b@h3.local", "passphrase-2")
    await jobs.add(a["id"], "mine")
    await jobs.add(b["id"], "their secret prompt")
    o = await _orch(app_settings)
    try:
        snap = await o.snapshot_for(a["id"])
        assert snap["counts"]["queued"] == 1
        assert snap["queue"]["total_queued"] == 2       # a number, not a list
        assert "secret prompt" not in str(snap)
    finally:
        await o.stop()


async def test_startup_requeues_a_job_orphaned_by_a_previous_process(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "orphan")
    await jobs.update(jid, status="running", remote_id="r1")
    o = await _orch(app_settings)
    try:
        assert (await jobs.get_any(jid))["status"] == "queued"
    finally:
        await o.stop()


async def test_the_lock_connection_does_not_sit_in_a_transaction(db, app_settings):
    """An idle-in-transaction connection pins the horizon and blocks VACUUM."""
    o = await _orch(app_settings)
    try:
        async with db.connection() as probe:
            row = await (await probe.execute(
                "SELECT count(*) AS n FROM pg_stat_activity"
                " WHERE state = 'idle in transaction'"
                "   AND datname = current_database()")).fetchone()
        assert row["n"] == 0
    finally:
        await o.stop()


# ---- B10: cancel sticks, teardown requeues ----

async def test_cancelling_a_running_job_sticks(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "changed my mind")
    backend, sink = FakeBackend(), FakeSink()
    o = await _orch(app_settings, backend, sink)
    try:
        await o.set_policy("auto")
        await o._tick()                                  # dispatched
        assert (await jobs.get_any(jid))["status"] == "running"
        assert await jobs.update_if(jid, "running", status="cancelled")
        await o._tick()                                  # render reports done
        assert (await jobs.get_any(jid))["status"] == "cancelled"
        assert sink.saved == []
    finally:
        await o.stop()


async def test_a_cancelled_job_whose_render_failed_is_not_requeued(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "changed my mind")
    o = await _orch(app_settings, FakeBackend(poll_state="failed"))
    try:
        await o.set_policy("auto")
        await o._tick()
        assert await jobs.update_if(jid, "running", status="cancelled")
        await o._tick()
        assert (await jobs.get_any(jid))["status"] == "cancelled"
    finally:
        await o.stop()


async def test_policy_off_mid_render_requeues_the_job(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "interrupted")
    backend = FakeBackend()
    o = await _orch(app_settings, backend)
    try:
        await o.set_policy("auto")
        await o._tick()
        assert (await jobs.get_any(jid))["status"] == "running"
        await o.set_policy("off")
        await o._tick()
        row = await jobs.get_any(jid)
        assert row["status"] == "queued" and row["remote_id"] is None
    finally:
        await o.stop()


# ---- B11: the user snapshot carries no admin detail ----

async def test_user_snapshot_hides_admin_notices(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    o = await _orch(app_settings)
    try:
        await o.set_policy("auto")
        assert "policy" not in str(await o.snapshot_for(u["id"])).lower()
        assert "policy" in (await o.snapshot())["notice"]
    finally:
        await o.stop()


async def test_user_snapshot_hides_money_after_a_budget_stop(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    await jobs.add(u["id"], "expensive")
    backend = FakeBackend()
    backend.cost = 999.0
    o = await _orch(app_settings, backend)
    try:
        await o.set_policy("auto")
        await o._tick()
        assert "$" not in str(await o.snapshot_for(u["id"]))
        assert "$" in (await o.snapshot())["notice"]
    finally:
        await o.stop()


# ---- a follower takes over when the leader goes away (rolling deploys) ----

async def test_a_follower_takes_over_when_the_leader_stops(db, app_settings, monkeypatch):
    import asyncio

    from app import orchestrator as orchestrator_mod
    monkeypatch.setattr(orchestrator_mod, "LEADER_RETRY_SECONDS", 0.05)

    old = await _orch(app_settings)
    new = Orchestrator(_cfg(app_settings), FakeBackend(), FakeSink(), FakeStorage())
    await new.start()
    try:
        assert old.leader is True and new.leader is False
        await old.stop()                        # the old container exits
        for _ in range(60):
            if new.leader:
                break
            await asyncio.sleep(0.05)
        assert new.leader is True
    finally:
        await new.stop()


async def test_the_new_leader_requeues_what_the_old_one_was_rendering(
        db, app_settings, monkeypatch):
    import asyncio

    from app import orchestrator as orchestrator_mod
    monkeypatch.setattr(orchestrator_mod, "LEADER_RETRY_SECONDS", 0.05)

    u = await users.create("a@h3.local", "passphrase-1")
    jid = await jobs.add(u["id"], "mid-render when the deploy happened")
    old = await _orch(app_settings)
    await old.set_policy("auto")
    await old._tick()
    assert (await jobs.get_any(jid))["status"] == "running"

    new = Orchestrator(_cfg(app_settings), FakeBackend(), FakeSink(), FakeStorage())
    await new.set_policy("off")                 # keep the new leader from re-claiming it
    await new.start()
    try:
        await old.stop()
        for _ in range(60):
            if new.leader:
                break
            await asyncio.sleep(0.05)
        assert new.leader is True
        assert (await jobs.get_any(jid))["status"] == "queued"
    finally:
        await new.stop()


async def test_stopping_releases_the_lock(db, app_settings):
    first = await _orch(app_settings)
    await first.stop()
    second = await _orch(app_settings)
    try:
        assert second.leader is True
    finally:
        await second.stop()
