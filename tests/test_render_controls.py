"""Per-clip render controls on top of a preset: steps, motion shift, render size, seed.

NULL means "whatever the preset says", so a clip that never opens the controls
renders exactly as before - the default graph must stay byte-identical.
"""
from __future__ import annotations

import pytest

from app import controls
from app.config import Config
from app.store import jobs
from app.workflows import SHIFT_NODE_ID, build_workflow, validate_graph
from tests.conftest import sign_in


def _graph(**job):
    base = {"prompt": "p", "mode": "t2v", "preset": "final", "seconds": 5}
    return build_workflow({**base, **job}, Config())


def _h3(graph):
    return next(n for n in graph.values() if n.get("class_type") == "MiniMaxH3ImageToVideo")


# ---- the graph ----

def test_a_clip_without_controls_renders_the_preset_exactly():
    plain = _graph(seed=1)
    again = _graph(seed=1, steps=None, shift_video=None, width=None, height=None)
    assert plain == again
    assert SHIFT_NODE_ID not in plain
    assert _h3(plain)["inputs"]["width"] != 1920


def test_steps_override_the_preset():
    steps = [n["inputs"]["steps"] for n in _graph(steps=12).values() if "steps" in n.get("inputs", {})]
    assert steps == [12]


def test_an_exact_render_size_becomes_literals_on_the_model_node():
    g = _graph(width=1920, height=1088)
    assert (_h3(g)["inputs"]["width"], _h3(g)["inputs"]["height"]) == (1920, 1088)
    # the selector fed nothing any more and is gone; the graph still validates
    assert not any(n.get("class_type") == "ResolutionSelector" for n in g.values())
    validate_graph(g)


def test_motion_splices_a_shift_node_after_the_lora():
    g = _graph(preset="turbo", shift_video=18.0)
    node = g[SHIFT_NODE_ID]
    assert node["class_type"] == "MiniMaxH3SigmaShift"
    assert node["inputs"]["shift_video"] == 18.0 and node["inputs"]["shift_audio"] == 3.0
    assert node["inputs"]["model"][0] == "h3_turbo_lora"
    consumers = [n for n in g.values() if n.get("inputs", {}).get("model") == [SHIFT_NODE_ID, 0]]
    assert len(consumers) >= 2      # the guider and the scheduler
    validate_graph(g)


def test_the_model_defaults_add_no_node():
    assert SHIFT_NODE_ID not in _graph(shift_video=12.0, shift_audio=3.0)


# ---- validation ----

@pytest.mark.parametrize("bad", [
    {"steps": 0}, {"steps": 61}, {"shift_video": 0.1}, {"width": 1920},
    {"width": 1000, "height": 576}, {"width": 2048, "height": 1152}, {"width": 128, "height": 128},
])
def test_out_of_range_controls_are_refused(bad):
    with pytest.raises(ValueError):
        controls.validate(bad.get("steps"), bad.get("shift_video"), bad.get("shift_audio"),
                          bad.get("width"), bad.get("height"))


async def test_the_route_refuses_bad_controls_with_a_reason(client, db):
    await sign_in(client)
    r = await client.post("/api/jobs", json={"prompts": "p", "width": 1000, "height": 576})
    assert r.status_code == 400 and "multiple of 32" in r.json()["detail"]


async def test_controls_are_stored_returned_and_copied_by_use_again(client, db):
    u = await sign_in(client)
    r = await client.post("/api/jobs", json={
        "prompts": "p", "steps": 24, "shift_video": 9.5, "width": 1920, "height": 1088})
    assert r.status_code == 200, r.text
    row = (await jobs.list_for(u["id"]))[0]
    assert (row["steps"], row["shift_video"], row["width"], row["height"]) == (24, 9.5, 1920, 1088)
    listed = (await client.get("/api/jobs")).json()["jobs"][0]
    assert listed["steps"] == 24 and listed["width"] == 1920
    await jobs.update(row["id"], status="done")
    copy = await jobs.get_any((await client.post(f"/api/jobs/{row['id']}/again")).json()["job_id"])
    assert (copy["steps"], copy["shift_video"], copy["width"]) == (24, 9.5, 1920)


async def test_the_estimate_prices_the_overrides(client, db):
    await sign_in(client)
    plain = (await client.post("/api/estimate", json={"prompts": "p", "preset": "final", "seconds": 5})).json()
    big = (await client.post("/api/estimate", json={
        "prompts": "p", "preset": "final", "seconds": 5, "width": 1920, "height": 1088})).json()
    assert big["minutes_per_clip"] > plain["minutes_per_clip"] * 1.8


def test_the_experimental_preset_exists_at_1080p():
    p = Config().generation.preset("hd1080")
    assert (p.width, p.height) == (1920, 1088)
