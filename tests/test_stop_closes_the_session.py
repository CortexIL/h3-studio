"""Stopping the process closes the GPU session it was running.

stop() used to delete the pod and nothing else: the runs row stayed open with
a $0 cost and the clip mid-render stayed "running" until the next process
noticed it. Every deploy did this, which is how the cost history came to
under-report and the runs table filled with sessions that never ended.
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


async def test_stop_closes_the_session_row_and_requeues_the_render(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    job_id = await jobs.add(u["id"], "a clip")
    o = await _orch(app_settings, _Slow())
    await o.set_policy("auto")
    await o._tick()
    assert (await jobs.get_any(job_id))["status"] == "running"
    await o.stop()
    row = (await runs.recent())[0]
    assert row["status"] == "stopped" and row["ended_at"] is not None
    assert "process stopping" in (row["note"] or "")
    assert (await jobs.get_any(job_id))["status"] == "queued"
