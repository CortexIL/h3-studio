"""Choosing a pod when capacity is scarce.

"There are no instances currently available" arrives as a 500 and says nothing
about the request, so the only useful answer is another card - and once the
whole list is exhausted, another cloud. A batch that waits all evening for one
GPU model is the failure this guards against.
"""
from __future__ import annotations

import pytest

from app.backends.runpod_pod import RunpodBackend, RunpodError
from app.config import Config


def _capacity_error() -> RunpodError:
    err = RunpodError("RunPod POST /pods -> 500")
    err.status = 500
    err.body = '{"error":"create pod: There are no instances currently available"}'
    return err


def _client_error() -> RunpodError:
    err = RunpodError("RunPod POST /pods -> 401")
    err.status = 401
    err.body = '{"error":"unauthorized"}'
    return err


def _backend(**runpod):
    cfg = Config()
    for key, value in runpod.items():
        setattr(cfg.runpod, key, value)
    return RunpodBackend(cfg), cfg


def _record(backend, fail_until=None):
    """Stub the API call, recording (gpu, cloud) per attempt."""
    attempts: list[tuple[str, str]] = []

    async def api(method, path, **kw):
        body = kw["json"]
        attempt = (body["gpuTypeIds"][0], body["cloudType"])
        attempts.append(attempt)
        if fail_until and fail_until(attempt):
            raise _capacity_error()
        return {"id": "pod-1", "costPerHr": 0.74}

    backend._api = api
    return attempts


async def test_the_preference_list_is_wider_than_one_card():
    cfg = Config()
    assert len(cfg.runpod.gpu_preference) >= 5
    assert cfg.runpod.cloud_fallback and cfg.runpod.cloud_fallback != cfg.runpod.cloud_type


async def test_it_moves_to_the_next_card_when_one_has_no_capacity():
    backend, cfg = _backend()
    first = cfg.runpod.gpu_preference[0]
    attempts = _record(backend, fail_until=lambda a: a[0] == first)
    try:
        await backend._create()
    finally:
        await backend.aclose()
    assert attempts[0] == (first, cfg.runpod.cloud_type)
    assert backend._gpu_used == cfg.runpod.gpu_preference[1]
    assert backend._pod_id == "pod-1"


async def test_the_first_card_on_the_dearer_cloud_beats_any_other_on_the_cheaper():
    """The 5090 rendered about 2.5x faster than any other card here, so even at
    the secure price it costs less per clip than anything community has."""
    backend, cfg = _backend()
    first = cfg.runpod.gpu_preference[0]
    primary, fallback = cfg.runpod.cloud_type, cfg.runpod.cloud_fallback
    attempts = _record(backend, fail_until=lambda a: a == (first, primary))
    try:
        await backend._create()
    finally:
        await backend.aclose()
    assert attempts == [(first, primary), (first, fallback)]
    assert backend._gpu_used == first and fallback in backend._detail


async def test_the_other_cards_still_take_the_cheaper_cloud_first():
    backend, cfg = _backend()
    first, rest = cfg.runpod.gpu_preference[0], cfg.runpod.gpu_preference[1:]
    primary, fallback = cfg.runpod.cloud_type, cfg.runpod.cloud_fallback
    attempts = _record(backend, fail_until=lambda a: a[0] == first or a[1] == primary)
    try:
        await backend._create()
    finally:
        await backend.aclose()
    assert attempts == ([(first, primary), (first, fallback)]
                        + [(gpu, primary) for gpu in rest] + [(rest[0], fallback)])
    assert backend._gpu_used == rest[0] and fallback in backend._detail


async def test_no_capacity_anywhere_says_it_is_availability_not_configuration():
    backend, cfg = _backend()
    attempts = _record(backend, fail_until=lambda a: True)
    try:
        with pytest.raises(RuntimeError, match="availability, not"):
            await backend._create()
    finally:
        await backend.aclose()
    assert len(attempts) == len(cfg.runpod.gpu_preference) * 2


async def test_a_rejected_request_stops_at_the_first_card():
    """A 401 or a malformed body would be rejected identically for every card."""
    backend, _ = _backend()
    attempts: list[tuple[str, str]] = []

    async def api(method, path, **kw):
        attempts.append((kw["json"]["gpuTypeIds"][0], kw["json"]["cloudType"]))
        raise _client_error()

    backend._api = api
    try:
        with pytest.raises(RunpodError):
            await backend._create()
    finally:
        await backend.aclose()
    assert len(attempts) == 1


async def test_the_fallback_can_be_switched_off():
    backend, cfg = _backend(cloud_fallback="")
    attempts = _record(backend, fail_until=lambda a: True)
    try:
        with pytest.raises(RuntimeError):
            await backend._create()
    finally:
        await backend.aclose()
    assert {a[1] for a in attempts} == {cfg.runpod.cloud_type}
