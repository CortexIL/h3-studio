"""A pod that leaves the account must not stall the queue.

A deleted pod disappears from the API rather than reporting TERMINATED, so
GET /pods/<id> answers 404. Treated as a generic error, that id can never come
back and every tick asks again anyway - one vanished pod held a 28 clip queue.
Ported from Oren Suchard's queue-reorder-and-pod-recovery, which shipped the
behaviour without tests.
"""
from __future__ import annotations

import time

import pytest

from app.backends.runpod_pod import GONE_AFTER_404S, RunpodBackend, RunpodError
from app.config import Config


def _backend() -> RunpodBackend:
    b = RunpodBackend(Config())
    b._pod_id = "p-vanished"
    b._started_at = time.time()
    return b


def _not_found() -> RunpodError:
    err = RunpodError("RunPod GET /pods/p-vanished -> 404: not found")
    err.status = 404
    return err


def _always_404(backend: RunpodBackend) -> None:
    async def fake_api(method: str, path: str, **kw):
        raise _not_found()

    backend._api = fake_api          # type: ignore[method-assign]


async def test_one_404_is_not_enough_to_forget_a_pod():
    """Seconds after a create, the pod can be missing from the API but real."""
    b = _backend()
    _always_404(b)
    st = await b.status()
    assert st.state == "booting"
    assert b._pod_id == "p-vanished"


async def test_a_pod_gone_for_good_is_dropped_so_the_next_pass_can_start_one():
    b = _backend()
    _always_404(b)
    for _ in range(GONE_AFTER_404S - 1):
        assert (await b.status()).state == "booting"
    final = await b.status()
    assert final.state == "off"
    assert b._pod_id is None
    assert "deleted outside this app" in b._detail


async def test_a_pod_that_answers_again_resets_the_count():
    """A blip must not accumulate across minutes into a false 'it is gone'."""
    b = _backend()
    replies: list[object] = [_not_found(), {"desiredStatus": "RUNNING"}, _not_found()]

    async def fake_api(method: str, path: str, **kw):
        reply = replies.pop(0)
        if isinstance(reply, RunpodError):
            raise reply
        return reply

    b._api = fake_api                # type: ignore[method-assign]
    await b.status()                 # 404
    await b.status()                 # answers, count resets
    await b.status()                 # 404 again, but only the first since the reset
    assert b._pod_id == "p-vanished"


async def test_a_pod_gone_from_the_account_keeps_what_it_cost():
    """Its hours were billed whether or not anyone still holds its id.

    Dropping the money with the clock is how three pods deleted from the RunPod
    console on 2026-10-05 went into the cost history at $0.00 while RunPod billed
    $8.51 for them, and how a replacement made mid-boot cut an hour's $0.80 to $0.003.
    """
    b = _backend()
    b._started_at = time.time() - 3600
    b._rate_per_hour = 1.0
    billed = b.cost_so_far()
    _always_404(b)
    for _ in range(GONE_AFTER_404S):
        await b.status()
    assert b._pod_id is None
    assert b.cost_so_far() == pytest.approx(billed, abs=0.001)
    # Shutting down is where the caller records it; the next pod starts at zero.
    await b.shutdown()
    assert b.cost_so_far() == 0.0


def test_the_cost_includes_the_container_disk():
    """RunPod bills the container disk on top of the GPU: $0.10 per GB a month
    while the pod runs, about two cents an hour at 140 GB - exactly the ~2% the
    cost history ran under RunPod's own bill."""
    b = RunpodBackend(Config())
    b._started_at = time.time() - 3600
    b._rate_per_hour = 0.99
    assert b.cost_so_far() == pytest.approx(0.99 + 140 * 0.10 / 730, rel=1e-3)
