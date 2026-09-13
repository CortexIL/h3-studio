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
from app.workflows import (BASE_TEMPLATE, DERIVE, HERE, TEMPLATES, WorkflowError,
                           build_workflow, unreachable_nodes, validate_graph)

OBJECT_INFO = Path(__file__).parent / "fixtures" / "comfy_object_info.json"

# The exported template carries two nodes nothing links to, and one of them
# (ImageScaleToTotalPixels) is missing its required `image` input entirely. ComfyUI
# never visits them, so they have never broken a render - but a derivation that
# linked to one would turn that missing input into a validation error, on a pod
# that is already booted and already being paid for. They are pruned on the way out.
KNOWN_ORPHANS = {"119", "120"}

BUILDABLE = sorted(set(TEMPLATES) | set(DERIVE))


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

@pytest.mark.parametrize("mode", BUILDABLE)
def test_every_link_in_a_built_graph_resolves(mode):
    assert validate_graph(build_workflow(_job(mode=mode), Config())) == []


@pytest.mark.parametrize("mode", BUILDABLE)
def test_nothing_unreachable_survives_into_a_built_graph(mode):
    assert unreachable_nodes(build_workflow(_job(mode=mode), Config())) == set()


def test_the_export_itself_still_carries_the_orphans_we_prune():
    """Pinned so that if a re-export ever cleans them up, the pruning can go too."""
    graph = json.loads((HERE / BASE_TEMPLATE).read_text(encoding="utf-8"))
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
    _, h3 = _one(graph, "MiniMaxH3ImageToVideo")

    # The prompt is written in the model's own format: the first-frame line,
    # then the description opening on [Shot 1].
    assert h3["inputs"]["prompt"] == (
        "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
        "\n\nintegrated_multimodal_description: [Shot 1] a prompt")
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


# ---------------------------------------------------------------- text to video

def test_text_to_video_carries_no_frame_and_no_loader():
    graph = build_workflow(_job(mode="t2v"), Config())
    _, h3 = _one(graph, "MiniMaxH3ImageToVideo")
    # Popped, not set to null: null is a value ComfyUI would try to consume.
    assert "first_frame" not in h3["inputs"]
    assert "last_frame" not in h3["inputs"]
    assert _of_class(graph, "LoadImage") == {}


def test_a_reference_sent_with_text_to_video_binds_to_nothing():
    """The mode ignores images by definition; it must not bind one by accident."""
    graph = build_workflow(_job(mode="t2v"), Config())
    assert _of_class(graph, "LoadImage") == {}


def test_text_to_video_keeps_everything_that_actually_renders():
    graph = build_workflow(_job(mode="t2v"), Config())
    _, h3 = _one(graph, "MiniMaxH3ImageToVideo")
    assert h3["inputs"]["prompt"] == "integrated_multimodal_description: [Shot 1] a prompt"
    for cls in ("UNETLoader", "CLIPLoader", "SamplerCustomAdvanced", "CreateVideo",
                "SaveVideo"):
        assert _of_class(graph, cls), f"the derivation lost {cls}"
    # The selector is gone on purpose: the canvas is written as exact numbers.
    assert not _of_class(graph, "ResolutionSelector")
    assert len(_of_class(graph, "VAELoader")) == 2, "video and audio VAEs are both needed"


def test_the_two_modes_differ_only_by_the_frame_and_its_loader():
    """The claim the whole derivation rests on."""
    i2v = build_workflow(_job(mode="i2v"), Config())
    t2v = build_workflow(_job(mode="t2v"), Config())
    loader_id, _ = _one(i2v, "LoadImage")
    assert set(i2v) - set(t2v) == {loader_id}
    assert set(t2v) - set(i2v) == set()


# ---------------------------------------------------------------- start to end

def _flf2v():
    return build_workflow(
        _job(mode="flf2v", ref_images=["uploads/u1/start.png", "uploads/u1/end.png"]),
        Config())


def test_start_to_end_binds_the_two_frames_to_the_two_ends():
    graph = _flf2v()
    _, h3 = _one(graph, "MiniMaxH3ImageToVideo")
    start_id, end_id = h3["inputs"]["first_frame"][0], h3["inputs"]["last_frame"][0]
    assert start_id != end_id, "both frames resolved to the same loader"
    assert graph[start_id]["inputs"]["image"] == "start.png"
    assert graph[end_id]["inputs"]["image"] == "end.png"


def test_the_frames_cannot_swap_when_the_file_order_changes():
    """The failure this mode is most exposed to, and the quietest.

    Two LoadImage nodes make "the first one" a property of JSON key order, which
    no one controls and a re-export can change. Reversing the graph must not move
    a single binding.
    """
    from app.workflows import _plan
    graph = _flf2v()
    plan = _plan(graph)
    shuffled = _plan(dict(reversed(list(graph.items()))))
    assert plan["first_frame"] == shuffled["first_frame"]
    assert plan["last_frame"] == shuffled["last_frame"]
    assert plan["first_frame"] != plan["last_frame"]


def test_start_to_end_is_the_reference_graph_plus_one_loader():
    i2v = build_workflow(_job(mode="i2v"), Config())
    assert set(_flf2v()) - set(i2v) == {"h3_end_frame"}


# ---------------------------------------------------------------- extend

def _extend():
    return build_workflow(_job(mode="extend", ref_images=["uploads/u1/tail.mp4"]), Config())


def test_extend_anchors_the_source_tail_at_the_very_front():
    graph = _extend()
    _, guide = _one(graph, "MiniMaxH3AddGuide")
    parts_id, _ = _one(graph, "GetVideoComponents")
    _, loader = _one(graph, "LoadVideo")
    h3_id, _ = _one(graph, "MiniMaxH3ImageToVideo")

    assert loader["inputs"]["file"] == "tail.mp4"
    assert guide["inputs"]["frame_idx"] == 0, "the new clip opens where the old one ended"
    assert guide["inputs"]["image"] == [parts_id, 0]
    assert guide["inputs"]["audio"] == [parts_id, 1], "the sound has to carry across too"
    assert guide["inputs"]["positive"] == [h3_id, 0]
    assert guide["inputs"]["latent"] == [h3_id, 1]


def test_extend_does_not_mix_up_the_two_vaes():
    """They look alike from outside and sit next to each other in the export;
    swapping them is silent until a pod runs the graph."""
    graph = _extend()
    _, guide = _one(graph, "MiniMaxH3AddGuide")
    _, h3 = _one(graph, "MiniMaxH3ImageToVideo")
    _, audio_decode = _one(graph, "VAEDecodeAudio")
    assert guide["inputs"]["vae"] == h3["inputs"]["vae"]
    assert guide["inputs"]["audio_vae"] == audio_decode["inputs"]["vae"]
    assert guide["inputs"]["vae"] != guide["inputs"]["audio_vae"]


def test_the_sampler_reads_the_guide_rather_than_the_bare_prompt():
    """If the rewiring missed, the clip renders from the prompt alone - a fine
    clip that simply does not continue anything."""
    graph = _extend()
    guide_id, _ = _one(graph, "MiniMaxH3AddGuide")
    _, guider = _one(graph, "BasicGuider")
    assert guider["inputs"]["conditioning"] == [guide_id, 0]


def test_extend_carries_no_still_frame():
    graph = _extend()
    _, h3 = _one(graph, "MiniMaxH3ImageToVideo")
    assert "first_frame" not in h3["inputs"]
    assert "last_frame" not in h3["inputs"]
    assert _of_class(graph, "LoadImage") == {}


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
@pytest.mark.parametrize("mode", BUILDABLE)
def test_no_graph_sets_an_input_the_node_never_declared(mode):
    """The check that catches the expensive failure: an input ComfyUI silently drops."""
    info = json.loads(OBJECT_INFO.read_text(encoding="utf-8"))
    assert validate_graph(build_workflow(_job(mode=mode), Config()), info) == []


def test_an_extension_can_be_told_where_to_arrive():
    """The guide fixes where a clip comes from; only a last frame says where it
    ends up, which is the one thing extend cannot be asked for in words."""
    graph = build_workflow(
        _job(mode='extend', ref_images=['uploads/u1/tail.mp4', 'uploads/u1/arrive.png']),
        Config())
    _, h3 = _one(graph, 'MiniMaxH3ImageToVideo')
    _, loader = _one(graph, 'LoadImage')
    _one(graph, 'MiniMaxH3AddGuide')                  # still a real extension
    assert loader['inputs']['image'] == 'arrive.png'
    assert h3['inputs']['last_frame'] == ['h3_end_frame', 0]
    assert 'first_frame' not in h3['inputs']          # the guide supplies the opening


def test_an_extension_with_no_destination_carries_no_loader():
    """An unused LoadImage would point at a file that is not on the pod, and the
    whole graph would be refused."""
    graph = build_workflow(_job(mode='extend', ref_images=['uploads/u1/tail.mp4']), Config())
    assert _of_class(graph, 'LoadImage') == {}
    _, h3 = _one(graph, 'MiniMaxH3ImageToVideo')
    assert 'last_frame' not in h3['inputs']
