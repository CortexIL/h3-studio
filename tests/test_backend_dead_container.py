"""A pod whose container never starts must not bill for the whole boot ceiling.

The ceiling is two hours because an 84GB weight download needs it. But a
download *talks*: the in-pod bootstrap answers within a minute or two and keeps
answering. A container that cannot start says nothing at all, and from outside
the two look identical - both sit in `booting`.

On 2026-09-28 four of five pods crashlooped on
`mount src=/dev/dri/card2 ... no such file or directory`, a host missing the GPU
device node. Each would have billed two A100-hours to render nothing. Silence is
the one thing a download and a dead container do not share, so it is what these
tests pin.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from app.backends import runpod_pod
from app.backends.runpod_pod import RunpodBackend
from app.config import Config


def _backend(silence_minutes: int) -> RunpodBackend:
    cfg = Config()
    cfg.pod.bootstrap_silence_minutes = silence_minutes
    b = RunpodBackend(cfg)
    b._pod_id = "p-1"
    b._started_at = time.time()

    async def running(method: str, path: str, **kw: Any) -> dict[str, Any]:
        return {"desiredStatus": "RUNNING", "id": "p-1"}

    b._api = running                          # type: ignore[method-assign]
    b._endpoint = lambda: "https://pod.test"  # type: ignore[method-assign]
    return b


class _Dead:
    """ComfyUI never comes up, because the container never ran."""

    async def is_alive(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


async def test_a_silent_pod_is_given_up_on_long_before_the_boot_ceiling():
    b = _backend(silence_minutes=0)
    b._comfy = _Dead()                        # type: ignore[assignment]

    async def never_answers() -> str:
        return "starting up (no status yet)"

    b._bootstrap_progress = never_answers     # type: ignore[method-assign]

    started = time.time()
    with pytest.raises(RuntimeError) as e:
        await b.ensure_ready()
    assert "never started" in str(e.value)
    assert "p-1" in str(e.value)
    # The point of the whole change: it does not sit there for two hours. One
    # poll interval can pass first - with the mark at zero the comparison sits
    # exactly on it, and a coarse clock does not always tick inside one pass.
    assert time.time() - started < 60
    assert b.cfg.pod.boot_timeout_minutes == 120, "the ceiling itself is unchanged"


async def test_a_pod_that_answered_once_is_left_alone_to_finish_downloading():
    """The failure mode this must not cause: killing a real 84GB download."""
    b = _backend(silence_minutes=0)
    polls = {"n": 0}

    class _Slow:
        async def is_alive(self) -> bool:
            polls["n"] += 1
            return polls["n"] > 1             # ready on the second look

        async def aclose(self) -> None:
            return None

    b._comfy = _Slow()                        # type: ignore[assignment]

    async def downloading() -> str:
        b._bootstrap_seen = True              # what the real probe does on a 503
        return "file 2 of 21"

    b._bootstrap_progress = downloading       # type: ignore[method-assign]

    st = await b.ensure_ready()
    assert st.state == "ready"


async def test_the_real_probe_records_that_the_pod_spoke():
    """If this line is lost, every slow download is killed at the silence mark."""
    b = _backend(silence_minutes=10)

    class _Resp:
        status_code = 503

        @staticmethod
        def json() -> dict[str, str]:
            return {"h3studio_bootstrap": "downloading weights: file 2 of 21"}

    class _Client:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            return _Resp()

    original, runpod_pod.httpx.AsyncClient = runpod_pod.httpx.AsyncClient, _Client
    try:
        assert b._bootstrap_seen is False
        detail = await b._bootstrap_progress()
    finally:
        runpod_pod.httpx.AsyncClient = original

    assert "file 2 of 21" in detail
    assert b._bootstrap_seen is True


async def test_silence_is_measured_from_the_boot_not_from_forever():
    """A pod that has not yet reached the mark is still given its chance."""
    b = _backend(silence_minutes=10)
    b._comfy = _Dead()                        # type: ignore[assignment]
    calls = {"n": 0}

    async def quiet_then_ready() -> str:
        calls["n"] += 1
        if calls["n"] >= 2:
            b._bootstrap_seen = True
            b._comfy = _Slow_ready()          # type: ignore[assignment]
        return "starting up (no status yet)"

    class _Slow_ready:
        async def is_alive(self) -> bool:
            return True

        async def aclose(self) -> None:
            return None

    b._bootstrap_progress = quiet_then_ready  # type: ignore[method-assign]
    st = await b.ensure_ready()
    assert st.state == "ready" and calls["n"] >= 2
