"""Turning a job row into a ComfyUI API-format workflow.

The design that matters, because it was learned the expensive way:

ComfyUI ships official MiniMax H3 templates. Rather than hand-writing node graphs -
and re-writing them whenever ComfyUI changes an input name - we treat *the exported
template as the source of truth* and only patch values into it. You export it once
(Workflow -> Export (API)) into this folder.

An earlier version of this module patched by guessing where things live: prompt in a
CLIPTextEncode, steps in a node named *Sampler*, length as a frame count. The real
template has none of those. It carries:

    MiniMaxH3ImageToVideo   prompt as a literal; width/height/length as *links*
    BasicScheduler          steps
    RandomNoise             noise_seed
    PrimitiveFloat          duration in seconds, fed through a math expression
    ResolutionSelector      megapixels + aspect ratio, driving width/height

So the rules here are structural rather than name-based:

  * never overwrite an input that is a link - that would sever the graph
  * find each target by what it *is*, and report what was found

`describe_patch_plan` exposes that so `app.doctor` can show which knobs a template
actually offers, instead of discovering a mismatch mid-batch.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from ..modes import DEFAULT_MODE, REF_SLOTS

HERE = Path(__file__).resolve().parent

# The one graph that came out of ComfyUI. Every other mode is derived from it,
# because they are the same graph with different optional inputs of one node
# connected - four near-identical exports would mean re-exporting this one leaves
# three stale copies, which is exactly the drift this module was written to avoid.
BASE_TEMPLATE = "h3_i2v.api.json"

#: Modes with an exported template of their own. A mode listed here wins over its
#: derivation, so a genuinely different graph (r2v uses another node entirely) can
#: be dropped in later without touching anything else.
TEMPLATES = {"i2v": BASE_TEMPLATE}

H3_NODE = "MiniMaxH3ImageToVideo"

# ComfyUI encodes a connection as [source_node_id, output_index].
def is_link(value: Any) -> bool:
    return (isinstance(value, list) and len(value) == 2
            and isinstance(value[1], int) and not isinstance(value[0], (int, float)))


class WorkflowError(RuntimeError):
    pass


def _h3_node(graph: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The conditioning node every mode is built around, found by class not by id."""
    for nid, node in graph.items():
        if isinstance(node, dict) and node.get("class_type") == H3_NODE:
            return nid, node
    raise WorkflowError(f"{BASE_TEMPLATE} has no {H3_NODE} node to build a mode from")


def _to_t2v(graph: dict[str, Any]) -> None:
    """Prompt only: drop the first frame, and the node that loaded it.

    A deletion rather than an addition, which is why this is the derivation to
    trust first: if ComfyUI ever renames the input, popping a key that is no longer
    there leaves an image bound and the validator says so, instead of silently
    rendering something else.
    """
    _, h3 = _h3_node(graph)
    link = h3["inputs"].pop("first_frame", None)
    h3["inputs"].pop("last_frame", None)
    if is_link(link):
        graph.pop(str(link[0]), None)


#: An id of our own, so the end frame's loader is never confused with the start
#: frame's. Two LoadImage nodes make "the first one" a property of JSON key order,
#: and two frames arriving the wrong way round is a failure nothing reports: the
#: clip renders, beautifully, backwards.
END_FRAME_LOADER_ID = "h3_end_frame"


def _to_flf2v(graph: dict[str, Any]) -> None:
    """Start and end: the base graph plus a loader wired to the last frame."""
    _, h3 = _h3_node(graph)
    graph[END_FRAME_LOADER_ID] = {
        "class_type": "LoadImage",
        "_meta": {"title": "Load Image (end frame)"},
        "inputs": {"image": "end.png"},
    }
    h3["inputs"]["last_frame"] = [END_FRAME_LOADER_ID, 0]


LOAD_VIDEO_ID = "h3_source_video"
VIDEO_PARTS_ID = "h3_source_parts"
GUIDE_ID = "h3_guide"


def _audio_vae_link(graph: dict[str, Any]) -> list[Any]:
    """The audio VAE, found through the node that decodes audio with it.

    Both VAELoaders look alike from the outside and sit next to each other in the
    export; picking the wrong one is silent until a pod runs the graph.
    """
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == "VAEDecodeAudio":
            link = (node.get("inputs") or {}).get("vae")
            if is_link(link):
                return link
    raise WorkflowError("the template has no audio VAE, so a guide's sound cannot be read")


def _rewire(graph: dict[str, Any], old: list[Any], new: list[Any]) -> None:
    """Point every input reading `old` at `new` instead."""
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        for field, value in (node.get("inputs") or {}).items():
            if is_link(value) and list(value) == list(old):
                node["inputs"][field] = list(new)


def _to_extend(graph: dict[str, Any]) -> None:
    """Continue a clip: its last frames, and their sound, anchored at the front.

    The guide is what makes this more than "start on the same picture" - the new
    clip carries the old one's movement and audio across the join, because those
    frames are anchored rather than merely described.
    """
    h3_id, h3 = _h3_node(graph)
    h3["inputs"].pop("first_frame", None)
    h3["inputs"].pop("last_frame", None)
    video_vae = h3["inputs"]["vae"]
    audio_vae = _audio_vae_link(graph)

    # Rewire before inserting the guide, or the guide's own `positive` input -
    # which reads the H3 node - would be redirected to itself.
    _rewire(graph, [h3_id, 0], [GUIDE_ID, 0])

    graph[LOAD_VIDEO_ID] = {
        "class_type": "LoadVideo",
        "_meta": {"title": "Load Video (the clip being continued)"},
        "inputs": {"file": "source.mp4"},
    }
    graph[VIDEO_PARTS_ID] = {
        "class_type": "GetVideoComponents",
        "_meta": {"title": "Get Video Components"},
        "inputs": {"video": [LOAD_VIDEO_ID, 0]},
    }
    graph[GUIDE_ID] = {
        "class_type": "MiniMaxH3AddGuide",
        "_meta": {"title": "Anchor the tail of the source clip"},
        "inputs": {
            "positive": [h3_id, 0],
            "latent": [h3_id, 1],
            "vae": video_vae,
            "audio_vae": audio_vae,
            "image": [VIDEO_PARTS_ID, 0],
            "audio": [VIDEO_PARTS_ID, 1],
            # Frame 0: the new clip opens on the old one's last moment.
            "frame_idx": 0,
        },
    }


#: How each mode without its own export is built from the base graph.
DERIVE = {"t2v": _to_t2v, "flf2v": _to_flf2v, "extend": _to_extend}


def _prune_unreachable(graph: dict[str, Any]) -> None:
    """Drop nodes nothing saved depends on.

    The export carries two of them, and one is missing a required input. ComfyUI
    never visits them so they have never broken a render, but they are a trap the
    moment a derivation links to one - and a graph that says only what it means is
    easier to check. Removing them cannot change the picture: by definition no
    output reads them.
    """
    for nid in unreachable_nodes(graph):
        graph.pop(nid, None)


def _load_template(mode: str) -> dict[str, Any]:
    # An unknown mode used to fall back to the t2v template, which turned a typo
    # into a clip rendered in the wrong mode - or, once t2v had no template, into
    # an error naming a file the caller never asked for.
    name = TEMPLATES.get(mode)
    derive = DERIVE.get(mode)
    if name is None and derive is None:
        known = ", ".join(sorted(set(TEMPLATES) | set(DERIVE)))
        raise WorkflowError(f"unknown render mode {mode!r}; known modes are {known}")

    name = name or BASE_TEMPLATE
    path = HERE / name
    if not path.exists():
        raise WorkflowError(
            f"missing workflow template {name}.\n"
            "Create it once: open ComfyUI on the pod -> Templates -> MiniMax H3 "
            f"({mode.upper()}) -> Workflow -> Export (API) -> save as "
            f"app/workflows/{name}.\n"
            "This keeps the node graph owned by ComfyUI rather than guessed by this app."
        )
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise WorkflowError(f"{name} is not valid JSON: {e}") from e

    if derive is not None:
        derive(graph)
    _prune_unreachable(graph)
    return graph


def _plan(graph: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Locate each patchable value as (node_id, input_name).

    Only literals are considered. An input carrying a link is driven by another node,
    and writing a value over it disconnects the graph.
    """
    found: dict[str, tuple[str, str]] = {}

    def literal(node: dict[str, Any], key: str) -> bool:
        return key in node.get("inputs", {}) and not is_link(node["inputs"][key])

    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type", "")
        title = (node.get("_meta") or {}).get("title", "").lower()

        # Prompt: a literal `prompt` anywhere (H3 nodes carry it directly), else the
        # positive half of a classic text-encode pair.
        if "prompt" not in found:
            if literal(node, "prompt"):
                found["prompt"] = (nid, "prompt")
            elif "TextEncode" in cls and literal(node, "text") and "negative" not in title:
                found["prompt"] = (nid, "text")

        if "video" not in found and cls == "LoadVideo" and literal(node, "file"):
            found["video"] = (nid, "file")

        if "steps" not in found and literal(node, "steps"):
            found["steps"] = (nid, "steps")

        if "seed" not in found:
            for key in ("noise_seed", "seed"):
                if literal(node, key):
                    found["seed"] = (nid, key)
                    break

        # Duration in seconds. The template feeds a PrimitiveFloat through a math
        # expression that turns it into a frame count, so this is the only place a
        # length can be set without touching the linked `length` input.
        if "seconds" not in found and "duration" in title and literal(node, "value"):
            found["seconds"] = (nid, "value")

        if "megapixels" not in found and cls == "ResolutionSelector" \
                and literal(node, "megapixels"):
            found["megapixels"] = (nid, "megapixels")
        if "aspect" not in found and cls == "ResolutionSelector" \
                and literal(node, "aspect_ratio"):
            found["aspect"] = (nid, "aspect_ratio")

    # Image slots are found by following the H3 node's own inputs, never by taking
    # the first LoadImage in the file. Once there are two of them - a start frame
    # and an end frame - "first" is whatever order the export happened to
    # serialise, so the two could swap with nothing at all to show for it.
    h3 = next((item for item in graph.items()
               if isinstance(item[1], dict) and item[1].get("class_type") == H3_NODE), None)
    if h3 is not None:
        for role in ("first_frame", "last_frame"):
            link = (h3[1].get("inputs") or {}).get(role)
            if not is_link(link):
                continue
            src = graph.get(str(link[0]))
            if isinstance(src, dict) and src.get("class_type") == "LoadImage" \
                    and not is_link((src.get("inputs") or {}).get("image")):
                found[role] = (str(link[0]), "image")

    return found


def _closest_aspect(options_hint: str, width: int, height: int) -> str:
    """ResolutionSelector takes a label, not numbers. Pick the nearest common one."""
    target = width / max(1, height)
    labels = {"1:1 (Square)": 1.0, "16:9 (Widescreen)": 16 / 9, "9:16 (Portrait)": 9 / 16,
              "4:3 (Standard)": 4 / 3, "3:4 (Portrait)": 3 / 4, "21:9 (Cinematic)": 21 / 9}
    best = min(labels, key=lambda k: abs(labels[k] - target))
    # Keep whatever the template already had if it is already the closest match.
    return best if options_hint not in labels or abs(labels[options_hint] - target) > 0.05 \
        else options_hint


LORA_NODE_ID = "h3_turbo_lora"


def _apply_lora(graph: dict[str, Any], lora_name: str, strength: float) -> bool:
    """Splice a model-only LoRA in between the diffusion model and its consumers.

    The template has UNETLoader feeding both BasicGuider and BasicScheduler. Rather
    than hardcode those two ids - they are template-specific and would rot on the
    next export - this finds the loader and redirects every `model` input that reads
    from it, so any consumer the template grows later is caught too.
    """
    unet = next((nid for nid, n in graph.items()
                 if isinstance(n, dict) and n.get("class_type") == "UNETLoader"), None)
    if unet is None:
        return False

    graph[LORA_NODE_ID] = {
        "class_type": "LoraLoaderModelOnly",
        "_meta": {"title": "Turbo LoRA"},
        "inputs": {
            "model": [unet, 0],
            "lora_name": lora_name,
            "strength_model": float(strength),
        },
    }
    for nid, node in graph.items():
        if nid == LORA_NODE_ID or not isinstance(node, dict):
            continue
        for field, value in (node.get("inputs") or {}).items():
            if field == "model" and is_link(value) and value[0] == unet:
                node["inputs"][field] = [LORA_NODE_ID, 0]
    return True


def build_workflow(job: dict[str, Any], cfg: Any) -> dict[str, Any]:
    """Patch a job's values into the exported ComfyUI template."""
    mode = job.get("mode") or DEFAULT_MODE
    graph = _load_template(mode)
    preset = cfg.generation.preset(job.get("preset"))
    plan = _plan(graph)

    if "prompt" not in plan:
        raise WorkflowError(
            f"no place to put the prompt in {TEMPLATES.get(mode)}. Run "
            "`python -m app.doctor --check-workflows` to see what the template exposes."
        )

    seconds = max(4, min(15, int(job.get("seconds") or cfg.generation.default_seconds)))
    seed = job.get("seed")
    if seed is None:
        seed = random.randint(0, 2**31 - 1)

    def put(key: str, value: Any) -> None:
        if key in plan:
            nid, field = plan[key]
            graph[nid]["inputs"][field] = value

    put("prompt", str(job.get("prompt", ""))[:4000])
    put("steps", preset.steps)
    put("seed", seed)
    put("seconds", float(seconds))

    # Resolution goes through the template's own selector rather than being forced
    # onto width/height, which are links here.
    if "megapixels" in plan:
        put("megapixels", round(preset.width * preset.height / 1_000_000, 2))
    if "aspect" in plan:
        nid, field = plan["aspect"]
        put("aspect", _closest_aspect(graph[nid]["inputs"][field],
                                      preset.width, preset.height))

    # Which slot each reference feeds is decided by position, because upload keys
    # are random hex and carry no role of their own.
    for slot, key in zip(REF_SLOTS.get(mode, ()), job.get("ref_images") or []):
        put(slot, Path(str(key)).name)

    if getattr(preset, "lora", ""):
        _apply_lora(graph, preset.lora, getattr(preset, "lora_strength", 1.0))

    return graph


def describe_patch_plan(mode: str = "i2v") -> dict[str, Any]:
    """What a template exposes, for `app.doctor` to report."""
    graph = _load_template(mode)
    plan = _plan(graph)
    return {
        "nodes": len(graph),
        "patchable": {k: f"{graph[n]['class_type']}.{f}" for k, (n, f) in plan.items()},
        "missing": [k for k in ("prompt", "steps", "seed", "seconds", "first_frame")
                    if k not in plan],
    }


def normalize_models(graph: dict[str, Any],
                     options: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Point every model reference at a filename ComfyUI actually has.

    A template carries whatever names were selected when it was exported, and those
    go stale the moment the weights are laid out differently on the pod - which is
    exactly what happened: the export said "vae/minimax_h3_audio_vae_fp32.safetensors"
    and the fixed layout offers "minimax_h3_audio_vae_fp32.safetensors", so every
    submit was rejected before a single frame was rendered.

    Matching is by basename, so it survives the prefix appearing or disappearing.
    Returns the substitutions made, for logging.
    """
    changed: list[tuple[str, str]] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        for field, value in (node.get("inputs") or {}).items():
            if not field.endswith("_name") or not isinstance(value, str):
                continue
            available = options.get(field) or []
            if not available or value in available:
                continue
            want = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            match = next((a for a in available
                          if a.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] == want), None)
            if match:
                node["inputs"][field] = match
                changed.append((value, match))
    return changed


# Nodes that save something. ComfyUI walks back from these and never visits anything
# else, which is why an unreachable node is ignored rather than rejected.
OUTPUT_CLASSES = ("SaveVideo", "SaveImage", "SaveAudio", "SaveWEBM", "SaveAnimatedWEBP")


def unreachable_nodes(graph: dict[str, Any]) -> set[str]:
    """Node ids that nothing saved depends on.

    Harmless in themselves - the exported i2v template has carried two of them,
    one missing a required input, without ever breaking a render. They matter
    because the moment a new mode *links* to one, its missing input becomes a
    validation error, and that error arrives after the pod is booted and paid for.
    """
    seen: set[str] = set()
    stack = [nid for nid, node in graph.items()
             if isinstance(node, dict) and node.get("class_type") in OUTPUT_CLASSES]
    while stack:
        nid = stack.pop()
        if nid in seen or not isinstance(graph.get(nid), dict):
            continue
        seen.add(nid)
        for value in (graph[nid].get("inputs") or {}).values():
            if is_link(value):
                stack.append(str(value[0]))
    return {nid for nid in graph if nid not in seen}


def validate_graph(graph: dict[str, Any],
                   object_info: dict[str, Any] | None = None) -> list[str]:
    """Everything wrong with a graph. An empty list means it looks sound.

    A malformed graph is the cheap failure: `/prompt` rejects it in a second. The
    expensive one is a graph that renders while quietly ignoring an input, because
    ComfyUI does not complain about an input a node has never heard of - so a
    renamed `last_frame` would come back as a perfectly good single-frame clip, at
    full price, looking like a model quality problem rather than a bug.

    Structural checks run anywhere. Pass `object_info` - recorded from a live pod by
    `app.smoketest` - to also check every input name against what the node declares,
    which is the check that catches the expensive case.
    """
    problems: list[str] = []
    for nid, node in graph.items():
        if not isinstance(node, dict):
            problems.append(f"{nid}: not a node")
            continue
        cls = node.get("class_type")
        if not cls:
            problems.append(f"{nid}: has no class_type")
            continue
        inputs = node.get("inputs") or {}

        for field, value in inputs.items():
            if not is_link(value):
                continue
            src = str(value[0])
            if src == nid:
                problems.append(f"{nid}.{field}: links to itself")
            elif src not in graph:
                problems.append(f"{nid}.{field}: links to node {src}, which is not here")

        if object_info is None:
            continue
        spec = object_info.get(cls)
        if spec is None:
            problems.append(f"{nid}: ComfyUI has no node called {cls}")
            continue
        declared = dict((spec.get("input") or {}).get("required") or {})
        optional = dict((spec.get("input") or {}).get("optional") or {})
        for field in inputs:
            if field not in declared and field not in optional:
                problems.append(f"{nid}.{field}: {cls} does not accept an input by that name")
        for field in declared:
            if field not in inputs:
                problems.append(f"{nid}.{field}: {cls} requires it, and it is not set")
    return problems
