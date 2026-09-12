"""What the pod backend makes of ComfyUI's answers about one prompt.

ComfyUI keeps its queue in memory. A prompt is waiting in it, executing, finished
into the history - or unknown, which is what every prompt becomes the moment the
pod is replaced. The old poll could not tell unknown from waiting, and an
orchestrator that cannot tell them apart waits forever.
"""
from __future__ import annotations

import httpx

from app.backends.runpod_pod import RunpodBackend
from app.config import Config


class _Comfy:
    """Just enough of ComfyClient for poll() and cancel()."""

    def __init__(self, *, running=(), pending=(), history=None, down=False):
        self.running, self.pending = set(running), set(pending)
        self.hist = history or {}
        self.down = down
        self.interrupted = 0
        self.deleted: list[str] = []

    async def queued_ids(self):
        if self.down:
            raise httpx.ConnectError("proxy blip")
        return set(self.running), set(self.pending)

    async def history(self, prompt_id):
        return self.hist.get(prompt_id)

    async def interrupt(self):
        self.interrupted += 1

    async def delete_queued(self, prompt_id):
        self.deleted.append(prompt_id)


def _backend(comfy: _Comfy) -> RunpodBackend:
    b = RunpodBackend(Config())
    b._comfy = comfy                   # type: ignore[assignment]
    return b


async def test_a_prompt_waiting_in_the_queue_is_pending():
    assert (await _backend(_Comfy(pending=["p1"])).poll("p1")).state == "pending"


async def test_a_prompt_being_executed_is_running():
    assert (await _backend(_Comfy(running=["p1"])).poll("p1")).state == "running"


async def test_a_prompt_that_errored_is_failed_with_the_message():
    hist = {"p1": {"status": {
        "status_str": "error", "completed": False,
        "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]}}}
    res = await _backend(_Comfy(history=hist)).poll("p1")
    assert res.state == "failed" and "CUDA" in res.error


async def test_a_prompt_this_pod_has_never_heard_of_is_lost():
    """Not 'pending': pending is what it looked like before, forever."""
    assert (await _backend(_Comfy()).poll("p1")).state == "lost"


async def test_a_blip_talking_to_the_pod_is_not_a_lost_render():
    res = await _backend(_Comfy(down=True)).poll("p1")
    assert res.state == "running" and "transient_error" in res.meta


async def test_cancelling_a_waiting_prompt_deletes_it_from_the_queue():
    c = _Comfy(pending=["p1"], running=["other"])
    await _backend(c).cancel("p1")
    assert c.deleted == ["p1"] and c.interrupted == 0


async def test_cancelling_the_prompt_being_executed_interrupts_it():
    c = _Comfy(running=["p1"])
    await _backend(c).cancel("p1")
    assert c.interrupted == 1 and c.deleted == []


async def test_cancelling_a_prompt_that_is_not_there_touches_nothing():
    """/interrupt stops whatever is executing - someone else's clip, if ours is gone."""
    c = _Comfy(running=["other"])
    await _backend(c).cancel("p1")
    assert c.interrupted == 0 and c.deleted == []


async def test_a_blip_during_a_cancel_is_raised_not_swallowed():
    """The orchestrator logs it; silently doing nothing would look like success."""
    try:
        await _backend(_Comfy(down=True)).cancel("p1")
    except httpx.HTTPError:
        return
    raise AssertionError("the failure was swallowed")
