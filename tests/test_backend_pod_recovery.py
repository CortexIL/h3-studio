"""A pod that leaves the account must not stall the queue.

A deleted pod disappears from the API rather than reporting TERMINATED, so
GET /pods/<id> answers 404. Treated as a generic error, that id can never come
back and every tick asks again anyway - one vanished pod held a 28 clip queue.
Ported from Oren Suchard's queue-reorder-and-pod-recovery, which shipped the
behaviour without tests.
"""
from __future__ import annotations

import time

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
