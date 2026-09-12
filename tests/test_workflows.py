"""The graphs we hand ComfyUI, checked without renting anything.

`build_workflow` had no tests of its own, and the mock backend never calls it, so
until now nothing stood between a bad graph and a rented GPU. These tests are the
substitute for that judgement between pod sessions - most importantly the
characterization of the reference mode, which is what makes "the mode people use
today is unchanged" a fact rather than a hope.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Config
from app.workflows import (HERE, TEMPLATES, WorkflowError, build_workflow,
                           unreachable_nodes, validate_graph)

OBJECT_INFO = Path(__file__).parent / "fixtures" / "comfy_object_info.json"

# The exported i2v template carries two nodes nothing links to, and one of them
# (ImageScaleToTotalPixels) is missing its required `image` input entirely. ComfyUI
# never visits them, so they have never broken a render. Pinned here so that if a
# re-export ever cleans them up - or a new mode links to one - a test says so.
KNOWN_ORPHANS = {"119", "120"}

ON_DISK = sorted(mode for mode, name in TEMPLATES.items() if (HERE / name).exists())


def _job(**over):
    job = {"mode": "i2v", "prompt": "a prompt", "seconds": 5, "preset": "final",
           "ref_images": ["uploads/u1/frame.png"]}
    job.update(over)
    return job


def _of_class(graph, cls):
    return {nid: n for nid, n in graph.items()
            if isinstance(n, dict) and n.get("class_type") == cls}


def _one(graph, cls):
    found = _of_class(graph, cls)
    assert len(found) == 1, f"expected one {cls}, found {len(found)}"
    return next(iter(found.items()))


# ---------------------------------------------------------------- structure

@pytest.mark.parametrize("mode", ON_DISK)
def test_every_link_in_a_built_graph_resolves(mode):
    assert validate_graph(build_workflow(_job(mode=mode), Config())) == []


def test_the_only_unreachable_nodes_are_the_two_we_know_about():
    graph = build_workflow(_job(), Config())
    assert unreachable_nodes(graph) == KNOWN_ORPHANS


def test_an_unknown_mode_is_refused_rather_than_quietly_rendered():
    """It used to fall back to t2v, which turned a typo into the wrong clip."""
    with pytest.raises(WorkflowError) as e:
        build_workflow(_job(mode="not-a-mode"), Config())
    assert "not-a-mode" in str(e.value)


# ---------------------------------------------------------------- reference mode

def test_the_reference_graph_is_what_it_has_always_been():
    """The characterization guard: every new mode is measured against this."""
    graph = build_workflow(_job(), Config())
    h3_id, h3 = _one(graph, "MiniMaxH3ImageToVideo")

    assert h3["inputs"]["prompt"] == "a prompt"
    # One frame, anchored as the first. The model also takes a last_frame; the
    # mode people use today must not start sending one.
    assert "last_frame" not in h3["inputs"]

    loader_id, _ = _one(graph, "LoadImage")
    assert h3["inputs"]["first_frame"] == [loader_id, 0]
    assert graph[loader_id]["inputs"]["image"] == "frame.png", \
        "the name here must match what the orchestrator uploads to the pod"


def test_a_reference_beyond_the_first_is_not_bound_anywhere():
    """Extra references are uploaded to the pod and then ignored - pinning today's
    behaviour, because the start-to-end mode is about to give refs[1] a meaning."""
    graph = build_workflow(
        _job(ref_images=["uploads/u1/a.png", "uploads/u1/b.png"]), Config())
    assert [n["inputs"]["image"] for n in _of_class(graph, "LoadImage").values()] == ["a.png"]


def test_the_turbo_lora_is_reached_by_everything_that_reads_the_model():
    graph = build_workflow(_job(preset="turbo"), Config())
    unet_id, _ = _one(graph, "UNETLoader")
    lora = _of_class(graph, "LoraLoaderModelOnly")
    assert len(lora) == 1
    lora_id = next(iter(lora))

    for nid, node in graph.items():
        if nid == lora_id:
            continue
        model = (node.get("inputs") or {}).get("model")
        if isinstance(model, list):
            assert model[0] != unet_id, f"{nid} still reads the raw model, skipping the LoRA"


# ---------------------------------------------------------------- the frame grid

@pytest.mark.parametrize("seconds", range(4, 16))
def test_every_clip_is_long_enough_to_anchor_a_guide(seconds):
    """Mirrors the template's own Math Expression node.

    Extend anchors a 22-frame guide at frame 0, and MiniMaxH3AddGuide refuses a
    guide that does not fit. Since seconds is clamped to 4..15 everywhere, the
    shortest clip the app can ask for is still several times the guide.
    """
    f = max(5, round(seconds * 24))
    frames = f + (5 - (f % 17)) % 17
    assert frames % 17 == 5, "the model only accepts lengths on the 17k+5 grid"
    assert frames >= 22, "a 22-frame guide would not fit"


# ---------------------------------------------------------------- against a real pod

@pytest.mark.skipif(not OBJECT_INFO.exists(),
                    reason="no recorded node signatures yet - run app.smoketest")
@pytest.mark.parametrize("mode", ON_DISK)
def test_no_graph_sets_an_input_the_node_never_declared(mode):
    """The check that catches the expensive failure: an input ComfyUI silently drops."""
    info = json.loads(OBJECT_INFO.read_text(encoding="utf-8"))
    assert validate_graph(build_workflow(_job(mode=mode), Config()), info) == []
