"""Stopping the process hands its pods to the next one, with the books closed.

stop() used to delete the pod and nothing else: the runs row stayed open with
a $0 cost and the clip mid-render stayed "running" until the next process
noticed it. Then it deleted every pod properly, and the process that took over
after a deploy paid for every boot again - 20 to 60 minutes of weight download
per pod. Now the pods stay up for the next leader to adopt, and what this
process still owes is its ledger.
"""
from __future__ import annotations

from app.store import jobs, runs, users
from tests.fakes import FakeBackend
from tests.test_orchestrator import _orch


class _Slow(FakeBackend):
    """A render that never finishes on its own."""

    def __init__(self) -> None:
        super().__init__(poll_state="running")
        self.up = True


async def _rendering(app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    job_id = await jobs.add(u["id"], "a clip")
    backend = _Slow()
    o = await _orch(app_settings, backend)
    await o.set_policy("auto")
    await o._tick()
    assert (await jobs.get_any(job_id))["status"] == "running"
    return o, backend, job_id


async def test_stop_leaves_the_pod_up_and_closes_the_books(db, app_settings):
    o, backend, job_id = await _rendering(app_settings)
    await o.stop()
    assert backend.up and backend.shutdowns == 0
    assert backend.cancelled, "the pod is told to drop the render"
    row = (await runs.recent())[0]
    assert row["status"] == "stopped" and row["ended_at"] is not None
    assert "left running" in (row["note"] or "")
    assert (await jobs.get_any(job_id))["status"] == "queued"


async def test_with_the_policy_off_stop_still_stops_the_pod(db, app_settings):
    """Nobody adopts a pod while the policy is off, so it must not be left behind."""
    o, backend, job_id = await _rendering(app_settings)
    await o.set_policy("off")               # and stopped before a tick acts on it
    await o.stop()
    assert not backend.up and backend.shutdowns == 1
    assert "process stopping" in ((await runs.recent())[0]["note"] or "")
    assert (await jobs.get_any(job_id))["status"] == "queued"
