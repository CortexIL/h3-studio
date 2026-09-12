"""A create that reports failure can still have rented a GPU.

POST /pods is not atomic from the caller's side. If the response is lost after
RunPod has begun renting, the pod exists and this process has no id for it, so
no teardown, budget ceiling or idle timer can reach it. Observed in production:
the app said none of three cards had capacity while a pod it had just asked for
billed for four minutes.
"""
from __future__ import annotations

import httpx
import pytest

from app.backends.runpod_pod import RunpodBackend, RunpodError
from app.config import Config


def _backend(**runpod: object) -> RunpodBackend:
    cfg = Config()
    cfg.runpod.gpu_preference = ["NVIDIA GeForce RTX 5090", "NVIDIA L40S"]
    for k, v in runpod.items():
        setattr(cfg.runpod, k, v)
    return RunpodBackend(cfg)


def _stub(backend: RunpodBackend, on_post, pods_after_post):
    """Replace the HTTP layer: POST does what the test says, GET lists pods."""
    seen: dict[str, object] = {}

    async def fake_api(method: str, path: str, **kw):
        if method == "POST":
            seen["name"] = kw["json"]["name"]
            seen["gpu"] = kw["json"]["gpuTypeIds"][0]
            return on_post(kw["json"])
        return pods_after_post(seen)

    backend._api = fake_api          # type: ignore[method-assign]
    return seen


async def test_a_pod_left_by_a_failed_create_is_adopted_not_abandoned():
    b = _backend()

    def post(_body):
        raise httpx.ReadTimeout("response lost")

    seen = _stub(b, post, lambda s: [
        {"id": "wagv40rkr75pnr", "name": s["name"], "desiredStatus": "RUNNING",
         "costPerHr": 0.69, "machine": {"gpuDisplayName": "NVIDIA GeForce RTX 5090"}},
    ])

    await b._create()

    assert b._pod_id == "wagv40rkr75pnr"
    assert b._rate_per_hour == pytest.approx(0.69)
    assert b._gpu_used == "NVIDIA GeForce RTX 5090"
    assert seen["name"].startswith("h3studio-")


async def test_a_5xx_after_the_rent_began_is_recovered_too():
    """A capacity-looking error is not proof that nothing was created."""
    b = _backend()

    def post(_body):
        err = RunpodError("RunPod POST /pods -> 502: bad gateway")
        err.status = 502
        raise err

    _stub(b, post, lambda s: [
        {"id": "p-502", "name": s["name"], "desiredStatus": "RUNNING",
         "costPerHr": 0.99, "machine": {"gpuDisplayName": "NVIDIA L40S"}},
    ])

    await b._create()
    assert b._pod_id == "p-502"


async def test_a_genuine_capacity_failure_still_raises():
    """Nothing was created, so there is nothing to adopt and the caller must hear
    about it - otherwise a real outage looks like a successful start."""
    b = _backend()

    def post(_body):
        err = RunpodError("no capacity")
        err.status = 500
        raise err

    _stub(b, post, lambda _s: [])

    with pytest.raises(RuntimeError):
        await b._create()
    assert b._pod_id is None


async def test_another_pods_name_is_not_mistaken_for_ours():
    """The name is unique per attempt precisely so a pod belonging to something
    else on the same account is never swept up."""
    b = _backend()

    def post(_body):
        raise httpx.ReadTimeout("response lost")

    _stub(b, post, lambda _s: [
        {"id": "someone-else", "name": "h3studio-1700000000-aaaaaa",
         "desiredStatus": "RUNNING", "costPerHr": 0.69},
    ])

    with pytest.raises(RuntimeError):
        await b._create()
    assert b._pod_id is None


def test_each_attempt_asks_for_a_different_name():
    """Two creates inside one second must still be tellable apart."""
    b = _backend()
    names = {b.build_create_body("NVIDIA GeForce RTX 5090")["name"] for _ in range(20)}
    assert len(names) == 20
