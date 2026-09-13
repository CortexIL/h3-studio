"""Keyframes anywhere: images pinned at a moment inside the clip.

The first and last frames belong to the model node; everything in between goes
through the guide node, one guide per keyframe, chained on the conditioning.
"""
from __future__ import annotations

from app.config import Config
from app.orchestrator import Orchestrator
from app.store import jobs, users
from app.workflows import KEYFRAME_GUIDE_ID, build_workflow, frame_count, validate_graph
from tests.conftest import sign_in
from tests.fakes import FakeBackend, FakeSink, FakeStorage


def _guider(graph):
    return next(n for n in graph.values() if n.get("class_type") == "BasicGuider")


def test_the_frame_grid_matches_the_template_arithmetic():
    assert frame_count(5) == 124 and frame_count(9) == 226 and frame_count(15) == 362


def test_each_keyframe_becomes_a_guide_chained_on_the_conditioning():
    g = build_workflow({"prompt": "p", "mode": "t2v", "preset": "final", "seconds": 5,
                        "keyframes": [{"key": "u/mid.png", "at": 2.5, "name": "mid.png"},
                                      {"key": "u/late.png", "at": 4.0}]}, Config())
    first, second = g[KEYFRAME_GUIDE_ID.format(i=0)], g[KEYFRAME_GUIDE_ID.format(i=1)]
    assert first["inputs"]["frame_idx"] == 60 and second["inputs"]["frame_idx"] == 96
    assert second["inputs"]["positive"] == [KEYFRAME_GUIDE_ID.format(i=0), 0]
    assert _guider(g)["inputs"]["conditioning"] == [KEYFRAME_GUIDE_ID.format(i=1), 0]
    loaders = [n["inputs"]["image"] for n in g.values()
               if n.get("class_type") == "LoadImage" and "keyframe" in n["_meta"]["title"]]
    assert loaders == ["mid.png", "late.png"]     # the stored name wins, else the key's name
    validate_graph(g)


def test_a_keyframe_never_lands_on_the_first_or_last_frame():
    g = build_workflow({"prompt": "p", "mode": "t2v", "preset": "final", "seconds": 5,
                        # the graph clamps what the route would have refused
                        "keyframes": [{"key": "u/a.png", "at": 0.0}, {"key": "u/b.png", "at": 5.2}]}, Config())
    idx = sorted(g[KEYFRAME_GUIDE_ID.format(i=i)]["inputs"]["frame_idx"] for i in range(2))
    assert idx == [1, frame_count(5) - 2]


def test_keyframes_chain_after_the_extend_guide():
    g = build_workflow({"prompt": "p", "mode": "extend", "preset": "final", "seconds": 5,
                        "ref_images": ["u/tail.mp4"], "keyframes": [{"key": "u/a.png", "at": 3}]}, Config())
    assert g[KEYFRAME_GUIDE_ID.format(i=0)]["inputs"]["positive"] == ["h3_guide", 0]
    validate_graph(g)


async def test_the_route_keeps_owned_keys_inside_the_clip_in_order(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "p", "seconds": 6,
        "keyframes": [{"key": f"uploads/{u['id']}/late.png", "at": 4},
                      {"key": "uploads/someone-else/x.png", "at": 2},
                      {"key": f"uploads/{u['id']}/early.png", "at": 1.5}]})
    assert r.status_code == 200, r.text
    row = (await jobs.list_for(u["id"]))[0]
    assert [k["at"] for k in row["keyframes"]] == [1.5, 4]
    assert (await client.get("/api/jobs")).json()["jobs"][0]["keyframes"] == row["keyframes"]


async def test_a_keyframe_outside_the_clip_is_refused(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "p", "seconds": 5, "keyframes": [{"key": f"uploads/{u['id']}/a.png", "at": 5}]})
    assert r.status_code == 400 and "inside the clip" in r.json()["detail"]


async def test_use_again_keeps_the_keyframes(client, db):
    u = await sign_in(client)
    jid = await jobs.add(u["id"], "p", keyframes=[{"key": f"uploads/{u['id']}/a.png", "at": 2.0}])
    await jobs.update(jid, status="done")
    copy = await jobs.get_any((await client.post(f"/api/jobs/{jid}/again")).json()["job_id"])
    assert copy["keyframes"] == [{"key": f"uploads/{u['id']}/a.png", "at": 2.0}]


async def test_the_orchestrator_sends_keyframe_images_to_the_gpu(db, app_settings):
    u = await users.create("a@h3.local", "passphrase-1")
    key = f"uploads/{u['id']}/mid.png"
    await jobs.add(u["id"], "p", mode="t2v", keyframes=[{"key": key, "at": 2.0}])
    backend = FakeBackend()
    o = Orchestrator(Config.from_settings(app_settings), backend, FakeSink(),
                     FakeStorage({key: b"PNGDATA"}))
    await o.start(run_loop=False)
    try:
        await o.set_policy("auto")
        await o._tick()
        assert backend.uploaded == [(b"PNGDATA", "mid.png")]
        assert backend.submitted[0]["keyframes"] == [{"key": key, "at": 2.0, "name": "mid.png"}]
    finally:
        await o.stop()
