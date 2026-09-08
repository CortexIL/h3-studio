"""Turning a job row into a ComfyUI API-format workflow.

Design choice worth understanding, because it saves a lot of pain:

ComfyUI ships official MiniMax H3 templates (T2V / I2V / R2V). Rather than
hand-writing node graphs here - and re-writing them every time ComfyUI changes an
input name - we treat *the exported template as the source of truth* and only patch
values into it. You export it once from ComfyUI (Workflow -> Export (API)), drop the
file in this folder, and this module substitutes prompt, size, steps, seed and length.

Patching is done by `class_type`, never by node id, so a template that gets re-exported
with different ids keeps working.

`python -m app.doctor --dump-nodes` prints the real input names from a live pod, which
is how you resolve any mismatch definitively instead of guessing.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

# Template file per mode. Missing file -> clear error telling you how to make it.
TEMPLATES = {
    "t2v": "h3_t2v.api.json",
    "i2v": "h3_i2v.api.json",
    "r2v": "h3_r2v.api.json",
}

# Candidate input names for each thing we patch. ComfyUI nodes are not consistent
# about naming, so we try the ones that actually occur and take the first hit.
SIZE_KEYS = {"width": ("width",), "height": ("height",)}
LEN_KEYS = ("length", "num_frames", "frames", "seconds", "duration")
STEP_KEYS = ("steps", "num_steps")
SEED_KEYS = ("seed", "noise_seed")
TEXT_KEYS = ("text", "prompt", "positive_prompt", "string")

POSITIVE_HINTS = ("positive", "prompt")
NEGATIVE_HINTS = ("negative",)


class WorkflowError(RuntimeError):
    pass


def _load_template(mode: str) -> dict[str, Any]:
    name = TEMPLATES.get(mode, TEMPLATES["t2v"])
    path = HERE / name
    if not path.exists():
        raise WorkflowError(
            f"missing workflow template {name}.\n"
            "Create it once: open ComfyUI on the pod -> Templates -> MiniMax H3 "
            f"({mode.upper()}) -> Workflow -> Export (API) -> save as app/workflows/{name}.\n"
            "This keeps the node graph owned by ComfyUI rather than guessed by this app."
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise WorkflowError(f"{name} is not valid JSON: {e}") from e


def _set_first(inputs: dict[str, Any], keys: tuple[str, ...], value: Any) -> bool:
    for k in keys:
        if k in inputs:
            inputs[k] = value
            return True
    return False


def _is_negative(node: dict[str, Any], graph: dict[str, Any]) -> bool:
    """A CLIPTextEncode is 'the negative one' if a sampler references it as negative."""
    node_id = node.get("_id")
    for other in graph.values():
        if not isinstance(other, dict):
            continue
        for name, val in (other.get("inputs") or {}).items():
            if isinstance(val, list) and val and str(val[0]) == str(node_id):
                if any(h in name.lower() for h in NEGATIVE_HINTS):
                    return True
    return False


def build_workflow(job: dict[str, Any], cfg: Any) -> dict[str, Any]:
    """Patch a job's values into the exported ComfyUI template."""
    mode = job.get("mode") or "t2v"
    graph = _load_template(mode)
    preset = cfg.generation.preset(job.get("preset"))

    seconds = int(job.get("seconds") or cfg.generation.default_seconds)
    seconds = max(4, min(15, seconds))                    # H3 accepts 4-15
    frames = seconds * cfg.generation.fps
    seed = job.get("seed")
    if seed is None:
        seed = random.randint(0, 2**31 - 1)

    # Tag nodes with their own id so _is_negative can look them up.
    for nid, node in graph.items():
        if isinstance(node, dict):
            node["_id"] = nid

    patched = {"prompt": False, "size": False, "length": False, "steps": False, "seed": False}

    for node in graph.values():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type", "")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        # Text prompt: the positive encoder only.
        if "CLIPTextEncode" in cls or "TextEncode" in cls:
            if not _is_negative(node, graph):
                if _set_first(inputs, TEXT_KEYS, job.get("prompt", "")):
                    patched["prompt"] = True
            continue

        # Latent / video setup nodes carry size and length.
        if "Latent" in cls or "MiniMaxH3" in cls:
            if _set_first(inputs, SIZE_KEYS["width"], preset.width):
                patched["size"] = True
            _set_first(inputs, SIZE_KEYS["height"], preset.height)
            for k in LEN_KEYS:
                if k in inputs:
                    inputs[k] = seconds if k in ("seconds", "duration") else frames
                    patched["length"] = True
                    break

        # Samplers carry steps and seed.
        if "Sampler" in cls or "Ksampler" in cls or "KSampler" in cls:
            if _set_first(inputs, STEP_KEYS, preset.steps):
                patched["steps"] = True
            if _set_first(inputs, SEED_KEYS, seed):
                patched["seed"] = True

        # Reference images for i2v / r2v.
        refs = job.get("ref_images") or []
        if refs and "LoadImage" in cls and "image" in inputs:
            inputs["image"] = refs[0]

    for node in graph.values():
        if isinstance(node, dict):
            node.pop("_id", None)

    if not patched["prompt"]:
        raise WorkflowError(
            f"could not find a positive text input in template for mode '{mode}'. "
            "Run `python -m app.doctor --dump-nodes` against a live pod to see the "
            "real node inputs, then adjust TEXT_KEYS in app/workflows/__init__.py."
        )
    return graph


def describe_patch_support(mode: str = "t2v") -> dict[str, Any]:
    """Used by the doctor command to report what a template exposes."""
    graph = _load_template(mode)
    seen: dict[str, list[str]] = {}
    for node in graph.values():
        if isinstance(node, dict) and isinstance(node.get("inputs"), dict):
            seen[node.get("class_type", "?")] = sorted(node["inputs"].keys())
    return seen
