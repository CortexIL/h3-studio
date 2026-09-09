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

HERE = Path(__file__).resolve().parent

TEMPLATES = {
    "t2v": "h3_t2v.api.json",
    "i2v": "h3_i2v.api.json",
    "r2v": "h3_r2v.api.json",
}

# ComfyUI encodes a connection as [source_node_id, output_index].
def is_link(value: Any) -> bool:
    return (isinstance(value, list) and len(value) == 2
            and isinstance(value[1], int) and not isinstance(value[0], (int, float)))


class WorkflowError(RuntimeError):
    pass


def _load_template(mode: str) -> dict[str, Any]:
    name = TEMPLATES.get(mode, TEMPLATES["t2v"])
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
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise WorkflowError(f"{name} is not valid JSON: {e}") from e


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

        if "image" not in found and cls == "LoadImage" and literal(node, "image"):
            found["image"] = (nid, "image")

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
    mode = job.get("mode") or "t2v"
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

    refs = job.get("ref_images") or []
    if refs and "image" in plan:
        put("image", Path(str(refs[0])).name)

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
        "missing": [k for k in ("prompt", "steps", "seed", "seconds", "image")
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
