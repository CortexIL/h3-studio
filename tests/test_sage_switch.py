"""Sage Attention is a switch on the Admin page, off until somebody turns it on.

The pip-installable kernel ignores attention masks and nobody has compared its
output with the stock render, so it must never be applied to everyone's clips
by default - and when it is on, every submitted job says so, whichever backend
carries it.
"""
from __future__ import annotations

from app.backends.runpod_pod import RunpodBackend
from app.config import Config
from app.store import jobs, kv, users
from app.workflows import SAGE_NODE, SAGE_NODE_ID
from tests.conftest import sign_in
from tests.fakes import FakeBackend
from tests.test_orchestrator import _orch


async def _one_tick(app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    await jobs.add(u["id"], "a clip")
    backend = FakeBackend()
    o = await _orch(app_settings, backend)
    try:
        await o.set_policy("auto")
        await o._tick()
    finally:
        await o.stop()
    assert backend.submitted, "the tick should have dispatched the clip"
    return backend.submitted[0]


async def test_off_unless_switched_on(db, app_settings):
    assert (await _one_tick(app_settings))["sage"] is False


async def test_the_switch_reaches_the_backend(db, app_settings):
    await kv.set("sage_attention", "on")
    assert (await _one_tick(app_settings))["sage"] is True


async def test_the_admin_route_persists_the_switch(client, db):
    await sign_in(client, "admin@h3.local", role="admin")
    r = await client.post("/api/admin/sage", json={"enabled": True})
    assert r.status_code == 200 and r.json() == {"sage": True}
    assert await kv.get("sage_attention") == "on"
    assert (await client.get("/api/admin/status")).json()["sage"] is True
    await client.post("/api/admin/sage", json={"enabled": False})
    assert (await client.get("/api/admin/status")).json()["sage"] is False


class _Comfy:
    """Just enough ComfyUI for submit(): the node exists, nothing else is asked."""

    def __init__(self) -> None:
        self.graphs: list[dict] = []

    async def object_info(self, node=None):
        return {SAGE_NODE: {}}

    async def model_options(self):
        return {}

    async def queue_prompt(self, graph):
        self.graphs.append(graph)
        return "prompt-1"


async def _submitted_with(sage: bool | None) -> dict:
    b = RunpodBackend(Config())
    b._pod_id = "p"
    b._comfy = _Comfy()          # type: ignore[assignment]
    job = {"id": "j", "mode": "i2v", "prompt": "p", "seconds": 4, "preset": "turbo",
           "ref_images": ["a.png"]}
    if sage is not None:
        job["sage"] = sage
    await b.submit(job)
    return b._comfy.graphs[0]    # type: ignore[union-attr]


async def test_a_pod_with_the_node_still_renders_plain_when_the_switch_is_off():
    assert SAGE_NODE_ID not in await _submitted_with(False)


async def test_the_node_is_spliced_in_when_the_switch_is_on():
    assert SAGE_NODE_ID in await _submitted_with(True)
