"""Choosing a pod when capacity is scarce.

A card nobody has free says nothing about the request, so the only useful answer
is another card - and once the whole list is exhausted, another cloud. A batch
that waits all evening for one GPU model is the failure this guards against.

REST v1 said "no instances currently available" with a 500. v2 says it with a
400, the same status as a request that breaks a rule, and only its `detail`
tells the two apart. RunPod's own advice for a 400 is to try the next card.
"""
from __future__ import annotations

import pytest

from app.backends.runpod_pod import RunpodBackend, RunpodError
from app.config import Config


def _error(status: int, detail: str) -> RunpodError:
    err = RunpodError(f"RunPod POST /pods -> {status}")
    err.status = status
    err.body = f'{{"title":"error","status":{status},"detail":"{detail}"}}'
    return err


def _capacity_error() -> RunpodError:
    return _error(400, "this GPU could not be placed")


# The fallback order needs more than one card to show, and the shipped list is
# whatever the owner wants that week - it may be a single card.
CARDS = ["NVIDIA GeForce RTX 5090", "NVIDIA RTX PRO 6000 Blackwell Server Edition",
         "NVIDIA H100 PCIe"]


def _backend(**runpod):
    cfg = Config()
    cfg.runpod.gpu_preference = list(CARDS)
    # The fallback machinery, whatever the production default is.
    cfg.runpod.cloud_type, cfg.runpod.cloud_fallback = "COMMUNITY", "SECURE"
    for key, value in runpod.items():
        setattr(cfg.runpod, key, value)
    return RunpodBackend(cfg), cfg


def _record(backend, fail_until=None):
    """Stub the API call, recording (gpu, cloud) per attempt."""
    attempts: list[tuple[str, str]] = []

    async def api(method, path, **kw):
        body = kw["json"]
        attempt = (body["gpu"]["id"], body["cloud"])
        attempts.append(attempt)
        if fail_until and fail_until(attempt):
            raise _capacity_error()
        return {"id": "pod-1", "cost": 0.74}

    backend._api = api
    return attempts


async def test_the_shipped_default_takes_a_secure_pro_6000_when_no_5090_is_free():
    """The owner's choice on 2026-11-27: a secure 5090 whenever one is free,
    otherwise a secure PRO 6000 rather than no pod at all. Still no community."""
    rp = Config().runpod
    assert rp.gpu_preference[0] == "NVIDIA GeForce RTX 5090"
    assert rp.gpu_preference[1:]
    assert all("RTX PRO 6000" in gpu for gpu in rp.gpu_preference[1:])
    assert rp.cloud_type == "SECURE" and not rp.cloud_fallback
    backend = RunpodBackend(Config())
    attempts = _record(backend, fail_until=lambda a: a[0] == "NVIDIA GeForce RTX 5090")
    try:
        await backend._create()
    finally:
        await backend.aclose()
    assert attempts == [("NVIDIA GeForce RTX 5090", "SECURE"), (rp.gpu_preference[1], "SECURE")]
    assert backend._gpu_used == rp.gpu_preference[1]


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


@pytest.mark.parametrize("status", [401, 402, 422])
async def test_a_rejected_request_stops_at_the_first_card(status):
    """A bad key, an empty balance or a body v2 will not parse would be refused
    identically for every card."""
    backend, _ = _backend()
    attempts: list[tuple[str, str]] = []

    async def api(method, path, **kw):
        attempts.append((kw["json"]["gpu"]["id"], kw["json"]["cloud"]))
        raise _error(status, "refused")

    backend._api = api
    try:
        with pytest.raises(RunpodError):
            await backend._create()
    finally:
        await backend.aclose()
    assert len(attempts) == 1


async def test_a_pool_the_account_cannot_rent_is_skipped():
    """v2 answers 403 for a GPU pool this key has no access to. The next card
    may well be open, which is RunPod's own advice too."""
    backend, cfg = _backend()
    first = cfg.runpod.gpu_preference[0]

    async def api(method, path, **kw):
        if kw["json"]["gpu"]["id"] == first:
            raise _error(403, "no access to this pool")
        return {"id": "pod-1", "cost": 0.74}

    backend._api = api
    try:
        await backend._create()
    finally:
        await backend.aclose()
    assert backend._gpu_used == cfg.runpod.gpu_preference[1]
    assert backend._rate_per_hour == pytest.approx(0.74)


async def test_the_fallback_can_be_switched_off():
    backend, cfg = _backend(cloud_fallback="")
    attempts = _record(backend, fail_until=lambda a: True)
    try:
        with pytest.raises(RuntimeError):
            await backend._create()
    finally:
        await backend.aclose()
    assert {a[1] for a in attempts} == {cfg.runpod.cloud_type}


def test_blackwell_cards_take_hosts_on_any_newer_cuda():
    """On 2026-11-07 every free 5090 and PRO 6000 ran CUDA 13.2, which the list
    cannot name, so asking for 12.8-13.0 found no host at all."""
    backend, _ = _backend()
    for gpu in ("NVIDIA GeForce RTX 5090", "NVIDIA RTX PRO 6000 Blackwell Server Edition"):
        assert "allowedCudaVersions" not in backend.build_create_body(gpu)["gpu"]
    # An older card can still sit on a driver too old for the image.
    assert "12.8" in backend.build_create_body("NVIDIA H100 PCIe")["gpu"]["allowedCudaVersions"]


def test_the_create_body_speaks_v2():
    """v2 refuses a field it does not know with a 422, so a v1 name left in the
    body would stop every create. GPU settings nest under `gpu`, and with no
    `mounts` the pod gets no volume laid over /workspace."""
    backend, cfg = _backend()
    body = backend.build_create_body("NVIDIA GeForce RTX 5090", "SECURE")
    assert set(body) == {"name", "image", "gpu", "cloud", "disk", "ports", "entrypoint"}
    assert body["gpu"]["id"] == "NVIDIA GeForce RTX 5090" and body["gpu"]["count"] == 1
    assert body["gpu"]["minRamPerGpu"] >= 16
    assert body["cloud"] == "SECURE" and body["image"] == cfg.runpod.image
    assert body["disk"] == cfg.runpod.container_disk_gb
    # Overrides the image's ENTRYPOINT: as a command, the bootstrap would only
    # reach /start.sh as arguments and be ignored.
    assert body["entrypoint"][:2] == ["bash", "-lc"]


def test_a_network_volume_is_mounted_where_the_weights_go():
    backend, _ = _backend(network_volume_id="vol-1")
    body = backend.build_create_body("NVIDIA GeForce RTX 5090")
    assert body["mounts"] == {"network": [{"volumeId": "vol-1", "path": "/workspace"}]}
