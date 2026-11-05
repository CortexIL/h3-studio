"""RunPod's rate limit must neither cost a pod nor lose one.

On 2026-11-05 five pods asked GET /pods/<id> every few seconds until RunPod
answered 429 "rate limit exceeded for the hour window". A booting pod's start
failed on it, the DELETE after it failed too, and five pods went on billing with
nothing here holding their ids.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.backends.runpod_pod import CreatePace, RunpodBackend, RunpodError
from app.config import Config


def _err(status: int) -> RunpodError:
    err = RunpodError(f"RunPod -> {status}")
    err.status = status
    return err


def _backend(replies: list, calls: list) -> RunpodBackend:
    """A pod mid-download; the API answers `replies` in order, then RUNNING."""
    b = RunpodBackend(Config())
    b._pod_id = "p-1"
    b._started_at = time.time()

    async def fake_api(method, path, **kw):
        calls.append(method)
        reply = replies.pop(0) if replies else {"desiredStatus": "RUNNING"}
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def downloading():
        return "downloading 3/21"

    b._api = fake_api                       # type: ignore[method-assign]
    b._bootstrap_progress = downloading     # type: ignore[method-assign]
    return b


async def test_a_429_while_booting_is_not_a_failed_start():
    b = _backend([_err(429)], calls := [])
    try:
        st = await b.status()
    finally:
        await b.aclose()
    assert st.state == "booting" and st.detail == "downloading 3/21"
    assert calls == ["GET"]


async def test_a_running_pod_asks_the_api_once_a_minute_not_every_poll():
    b = _backend([], calls := [])
    try:
        for _ in range(20):
            await b.status()
    finally:
        await b.aclose()
    assert calls == ["GET"]


async def test_a_delete_that_fails_keeps_the_pod_so_it_can_be_stopped_again():
    b = _backend([_err(429)], calls := [])
    try:
        with pytest.raises(RunpodError):
            await b.shutdown()
        assert b._pod_id == "p-1"
        await b.shutdown()                  # the next try gets through
        assert b._pod_id is None
    finally:
        await b.aclose()
    assert calls == ["DELETE", "DELETE"]


async def test_a_pod_already_gone_counts_as_stopped():
    b = _backend([_err(404)], calls := [])
    try:
        await b.shutdown()
    finally:
        await b.aclose()
    assert b._pod_id is None


async def test_sibling_pods_ask_for_gpus_one_at_a_time():
    """Ten creates in one second drew a 429 on nine of them."""
    pace, inside, most = CreatePace(), [0], [0]

    async def api(method, path, **kw):
        inside[0] += 1
        most[0] = max(most[0], inside[0])
        await asyncio.sleep(0.01)
        inside[0] -= 1
        return {"id": kw["json"]["name"], "costPerHr": 0.69}

    pods = [RunpodBackend(Config(), pace=pace) for _ in range(5)]
    for b in pods:
        b._api = api                        # type: ignore[method-assign]
    try:
        await asyncio.gather(*(b._create() for b in pods))
    finally:
        for b in pods:
            await b.aclose()
    assert most[0] == 1 and all(b._pod_id for b in pods)


async def test_after_no_card_is_free_the_siblings_wait_instead_of_asking():
    pace, calls = CreatePace(), []

    async def api(method, path, **kw):
        calls.append(method)
        err = _err(500)
        err.body = '{"error":"create pod: There are no instances currently available"}'
        raise err

    pods = [RunpodBackend(Config(), pace=pace) for _ in range(2)]
    for b in pods:
        b._api = api                        # type: ignore[method-assign]
    try:
        for b in pods:
            with pytest.raises(RuntimeError, match="No capacity"):
                await b._create()
    finally:
        for b in pods:
            await b.aclose()
    # Each card on each cloud once, by the first pod only, and no stray hunt
    # after RunPod's own "none free".
    assert calls == ["POST"] * 2 * len(Config().runpod.gpu_preference)
