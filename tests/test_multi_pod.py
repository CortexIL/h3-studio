"""Several pods at once: more only when a long queue is worth their boot, each
closing on its own when idle, and one money ceiling over all of them."""
from __future__ import annotations

import asyncio
import itertools
import time

import pytest
import pytest_asyncio

from app import estimate
from app.backends import PodStatus
from app.backends.runpod_pod import RunpodBackend
from app.config import Config
from app.orchestrator import MAX_PODS, Orchestrator
from app.store import jobs, kv, runs, users
from tests.conftest import sign_in
from tests.fakes import FakeBackend, FakeSink, FakeStorage

_ids = itertools.count(1)


class PodBackend(FakeBackend):
    """A fake with its own pod id, so pods can be told apart."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.pod_id = f"pod-{next(_ids)}"

    async def status(self) -> PodStatus:
        return PodStatus(state="ready" if self.up else "off", pod_id=self.pod_id)

    async def ensure_ready(self, on_status=None) -> PodStatus:
        self.up = True
        return PodStatus(state="ready", pod_id=self.pod_id, endpoint="http://fake")


class Factory:
    def __init__(self, **kw) -> None:
        self.kw = kw
        self.made: list[PodBackend] = []

    def __call__(self) -> PodBackend:
        b = PodBackend(**self.kw)
        self.made.append(b)
        return b


_started: list[Orchestrator] = []


@pytest_asyncio.fixture(autouse=True)
async def _stop_leftovers(db):
    # Depends on `db` so it runs before the pool closes.
    yield
    while _started:
        o = _started.pop()
        if o._lock_conn is not None:
            await o.stop()


async def _orch(app_settings, factory: Factory) -> Orchestrator:
    o = Orchestrator(Config.from_settings(app_settings), factory(), FakeSink(),
                     FakeStorage(), backend_factory=factory)
    await o.start(run_loop=False)
    _started.append(o)
    await o.set_policy("auto")
    return o


async def _statuses(ids: list[str]) -> list[str]:
    return [(await jobs.get_any(j))["status"] for j in ids]


async def _ticks(o: Orchestrator, n: int) -> None:
    for _ in range(n):
        await o._tick()
        # Let pods booting in the background finish.
        for _ in range(5):
            await asyncio.sleep(0)


@pytest.fixture
def long_boot(monkeypatch):
    """A boot so slow no queue is ever worth a second pod."""
    monkeypatch.setattr(estimate, "startup_minutes", lambda cfg=None: 1e9)


@pytest.fixture
def quick_boot(monkeypatch):
    """A boot so quick any waiting clip is worth another pod."""
    monkeypatch.setattr(estimate, "startup_minutes", lambda cfg=None: 0.0)


async def _queue(n: int, prompt: str = "clip") -> list[str]:
    u = await users.create(f"{prompt}{next(_ids)}@h3.local", "passphrase-1")
    return [await jobs.add(u["id"], f"{prompt} {i}") for i in range(n)]


async def test_one_pod_unless_the_admin_asks_for_more(db, app_settings, quick_boot):
    factory = Factory(poll_state="running")
    await _queue(8)
    o = await _orch(app_settings, factory)
    await _ticks(o, 6)
    assert len(o.slots) == 1
    assert sum(b.up for b in factory.made) == 1


async def test_a_long_queue_brings_up_more_pods_up_to_the_limit(db, app_settings, quick_boot):
    factory = Factory(poll_state="running")
    ids = await _queue(8)
    o = await _orch(app_settings, factory)
    await o.set_max_pods(3)
    await _ticks(o, 8)
    assert len(o.slots) == 3
    assert sum(b.up for b in factory.made) == 3
    # One clip per pod, each on a different card.
    assert sorted(len(b.submitted) for b in factory.made) == [1, 1, 1]
    assert (await _statuses(ids)).count("running") == 3
    assert len(await runs.recent()) == 3


async def test_a_short_queue_is_not_worth_a_second_boot(db, app_settings, long_boot):
    factory = Factory(poll_state="running")
    await _queue(8)
    o = await _orch(app_settings, factory)
    await o.set_max_pods(5)
    await _ticks(o, 6)
    assert len(o.slots) == 1


async def test_more_pods_render_the_queue_in_fewer_ticks(db, app_settings, quick_boot):
    factory = Factory()
    ids = await _queue(6)
    o = await _orch(app_settings, factory)
    await o.set_max_pods(3)
    for tick in range(1, 20):
        await _ticks(o, 1)
        if set(await _statuses(ids)) == {"done"}:
            break
    assert tick <= 5
    assert sum(len(b.submitted) for b in factory.made) == 6


async def test_an_idle_extra_pod_closes_while_another_keeps_rendering(
        db, app_settings, quick_boot):
    factory = Factory(poll_state="running")
    await _queue(2)
    o = await _orch(app_settings, factory)
    await o.set_max_pods(2)
    await _ticks(o, 4)
    assert len(o.slots) == 2
    first, second = o.slots
    # The second pod's clip finishes; nothing else is waiting.
    second.backend.poll_state = "done"
    await _ticks(o, 1)
    assert not o._placed or all(s is first for s in o._placed.values())
    second.last_busy = time.time() - 3600
    await _ticks(o, 1)
    assert second.backend.up is False and second.backend.shutdowns == 1
    assert first.backend.up is True
    assert o.slots == [first]
    stopped = [r for r in await runs.recent() if r["status"] == "stopped"]
    assert len(stopped) == 1 and "idle" in stopped[0]["note"]


async def test_the_budget_covers_every_pod_together(db, app_settings, quick_boot):
    factory = Factory(poll_state="running")
    await _queue(4)
    o = await _orch(app_settings, factory)
    await o.set_max_pods(2)
    await _ticks(o, 3)
    assert len(o.slots) == 2
    # $5 each is under an $8 limit per pod, over it together.
    o.cfg.budget.session_limit_usd = 8.0
    for slot in o.slots:
        slot.backend.cost = 5.0
    await _ticks(o, 1)
    assert await o.policy() == "off"
    assert all(not b.up for b in factory.made)
    assert (await jobs.counts_all())["running"] == 0


async def test_policy_off_stops_every_pod_and_requeues(db, app_settings, quick_boot):
    factory = Factory(poll_state="running")
    ids = await _queue(3)
    o = await _orch(app_settings, factory)
    await o.set_max_pods(3)
    await _ticks(o, 5)
    assert sum(b.up for b in factory.made) == 3
    await o.set_policy("off")
    await _ticks(o, 1)
    assert all(not b.up for b in factory.made)
    assert set(await _statuses(ids)) == {"queued"}
    assert o._inflight == {} and len(o.slots) == 1


class FailsToStart(PodBackend):
    async def ensure_ready(self, on_status=None):
        raise RuntimeError("no capacity for any card just now")


async def test_one_pod_failing_to_start_does_not_hold_up_the_others(
        db, app_settings, quick_boot):
    made: list[PodBackend] = []

    def factory():
        b = PodBackend(poll_state="running") if not made else FailsToStart()
        made.append(b)
        return b

    ids = await _queue(3)
    o = Orchestrator(Config.from_settings(app_settings), factory(), FakeSink(),
                     FakeStorage(), backend_factory=factory)
    await o.start(run_loop=False)
    _started.append(o)
    await o.set_policy("auto")
    await o.set_max_pods(3)
    await _ticks(o, 4)
    assert made[0].up is True
    assert len(made) >= 2 and not any(b.up for b in made[1:])
    assert any(r["status"] == "error" for r in await runs.recent())
    assert "running" in await _statuses(ids)


async def test_the_admin_snapshot_lists_every_pod(db, app_settings, quick_boot):
    factory = Factory(poll_state="running")
    await _queue(4)
    o = await _orch(app_settings, factory)
    await o.set_max_pods(2)
    await _ticks(o, 3)
    snap = await o.snapshot()
    assert snap["max_pods"] == 2 and snap["max_pods_allowed"] == MAX_PODS
    assert [p["number"] for p in snap["pods"]] == [1, 2]
    assert {p["pod_id"] for p in snap["pods"]} == {b.pod_id for b in factory.made}
    assert all(p["rendering"] == 1 for p in snap["pods"])
    u = await users.create("look@h3.local", "passphrase-1")
    mine = await o.snapshot_for(u["id"])
    assert mine["pod"]["ready"] == 2
    assert "pod-" not in str(mine)


async def test_the_number_of_pods_is_bounded(db, app_settings):
    o = await _orch(app_settings, Factory())
    for bad in (0, MAX_PODS + 1):
        with pytest.raises(ValueError):
            await o.set_max_pods(bad)
    await kv.set("max_pods", "99")
    assert await o.max_pods() == MAX_PODS
    await kv.set("max_pods", "nonsense")
    assert await o.max_pods() == 1


async def test_without_a_factory_there_is_only_ever_one_pod(db, app_settings, quick_boot):
    await _queue(5)
    o = Orchestrator(Config.from_settings(app_settings), PodBackend(poll_state="running"),
                     FakeSink(), FakeStorage())
    await o.start(run_loop=False)
    _started.append(o)
    await o.set_policy("auto")
    await kv.set("max_pods", "4")
    await _ticks(o, 5)
    assert len(o.slots) == 1 and await o.max_pods() == 1


async def test_max_pods_route(client, db):
    await sign_in(client, "boss@h3.local", role="admin")
    assert (await client.post("/api/admin/max-pods", json={"max_pods": 0})).status_code == 400
    assert (await client.post("/api/admin/max-pods", json={"max_pods": 6})).status_code == 400
    r = await client.post("/api/admin/max-pods", json={"max_pods": 3})
    assert r.status_code == 200
    assert await kv.get("max_pods") == "3"


async def test_max_pods_route_is_admin_only(client, db):
    await sign_in(client, "u@h3.local", role="user")
    r = await client.post("/api/admin/max-pods", json={"max_pods": 3})
    assert r.status_code == 403


# ---- RunPod: two backends in one process never share a pod ----

def _runpod(claimed: set[str]) -> RunpodBackend:
    return RunpodBackend(Config(), claimed=claimed)


async def test_sibling_backends_adopt_different_pods():
    claimed: set[str] = set()
    listing = [
        {"id": "p-a", "name": "h3studio-1-aaaaaa", "desiredStatus": "RUNNING", "costPerHr": 0.69},
        {"id": "p-b", "name": "h3studio-2-bbbbbb", "desiredStatus": "RUNNING", "costPerHr": 0.69},
    ]
    backends = [_runpod(claimed) for _ in range(3)]
    for b in backends:
        async def fake_api(method, path, **kw):
            return listing
        b._api = fake_api                # type: ignore[method-assign]
    assert await backends[0].adopt_existing() == "p-a"
    assert await backends[1].adopt_existing() == "p-b"
    assert await backends[2].adopt_existing() is None
    assert claimed == {"p-a", "p-b"}
    for b in backends:
        await b.aclose()


async def test_a_released_pod_can_be_adopted_again():
    claimed: set[str] = set()
    b = _runpod(claimed)
    b._pod_id = "p-a"
    assert claimed == {"p-a"}
    b._pod_id = None
    assert claimed == set()
    await b.aclose()


# ---- the estimate knows about the extra pods ----

def test_the_estimate_shares_the_rendering_between_pods():
    cfg = Config()
    preset = cfg.generation.preset("final")
    one = estimate.estimate_batch("NVIDIA GeForce RTX 5090", 10, preset, 10, cfg)
    five = estimate.estimate_batch("NVIDIA GeForce RTX 5090", 10, preset, 10, cfg,
                                   max_pods=5)
    assert one["pods"] == 1 and five["pods"] == 5
    assert five["total_minutes"] < one["total_minutes"]
    # Faster, but every extra pod pays its own boot.
    assert five["cost_usd"] > one["cost_usd"]


def test_one_clip_never_gets_a_second_pod():
    assert estimate.pods_for(1, 60.0, 13.0, 5) == 1
    assert estimate.pods_for(3, 1.0, 13.0, 5) == 1
