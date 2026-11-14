"""Taking over a pod this app left running.

A deploy hands its pods to the next process, which finds them on the account by
their `h3studio-` name. Reading that list wrong costs a GPU either way: a pod
left behind bills unwatched, and the process rents another in its place.
"""
from __future__ import annotations

import pytest

from app.backends.runpod_pod import RunpodBackend
from app.config import Config


def _backend(status: str) -> RunpodBackend:
    """A backend whose GET /v2/pods lists one of our pods, in `status`."""
    b = RunpodBackend(Config())

    async def fake_api(method, path, **kw):
        return {"pods": [{"id": "p-a", "name": "h3studio-1-aaaaaa", "status": status,
                          "cost": 0.99, "gpu": {"id": "NVIDIA GeForce RTX 5090", "count": 1}}],
                "pagination": {"hasNextPage": False}}

    b._api = fake_api                    # type: ignore[method-assign]
    return b


async def test_an_adopted_pod_knows_its_card_and_rate():
    """Without the card name an adopted 5090 showed as "?" on the admin page."""
    b = _backend("RUNNING")
    try:
        assert await b.adopt_existing() == "p-a"
    finally:
        await b.aclose()
    assert b.gpu_used == "NVIDIA GeForce RTX 5090"
    assert b.rate_per_hour == pytest.approx(0.99)


@pytest.mark.parametrize("status", ["PROVISIONING", "STARTING", "ERROR"])
async def test_a_pod_handed_over_before_it_is_up_is_adopted(status):
    """REST v2 says what a pod is doing, not what it was asked to do. A deploy
    that lands while one is still starting must take it over, or the next
    process rents a second GPU while the first bills. One in ERROR is taken
    too: the boot that follows sees the error and stops it."""
    b = _backend(status)
    try:
        assert await b.adopt_existing() == "p-a"
    finally:
        await b.aclose()


@pytest.mark.parametrize("status", ["EXITED", "TERMINATED"])
async def test_a_stopped_pod_is_not_adopted(status):
    b = _backend(status)
    try:
        assert await b.adopt_existing() is None
    finally:
        await b.aclose()
