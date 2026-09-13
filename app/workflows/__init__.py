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

import re

from .. import controls
from .. import effects as effects_mod
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
        if isinstance(node, dict) and node.get("class_type") in (H3_NODE, "MiniMaxH3ReferenceToVideo"):
            return nid, node
    raise WorkflowError(f"{BASE_TEMPLATE} has no {H3_NODE} node to build a mode from")


def _to_t2v(graph: dict[str, Any], job: dict[str, Any] | None = None) -> None:
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


def _to_flf2v(graph: dict[str, Any], job: dict[str, Any] | None = None) -> None:
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


def _to_extend(graph: dict[str, Any], job: dict[str, Any] | None = None) -> None:
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

    # An extension may also be told where to arrive. The guide fixes where it
    # comes *from*; without a last frame the clip continues wherever the prompt
    # takes it, which is the one thing extend cannot be asked for in words.
    if len((job or {}).get("ref_images") or []) >= 2:
        graph[END_FRAME_LOADER_ID] = {
            "class_type": "LoadImage",
            "_meta": {"title": "Load Image (where the extension arrives)"},
            "inputs": {"image": "end.png"},
        }
        h3["inputs"]["last_frame"] = [END_FRAME_LOADER_ID, 0]


R2V_NODE = "MiniMaxH3ReferenceToVideo"
REF2VA_MODEL = "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors"
#: The turbo LoRA is trained per checkpoint; the preset names the fl2va one.
R2V_LORA = {
    "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors":
        "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
}
REF_IMAGE_ID = "h3_ref_img_{i}"
REF_VIDEO_ID = "h3_ref_vid_{i}"
REF_VIDEO_PARTS_ID = "h3_ref_vid_parts_{i}"
REF_AUDIO_ID = "h3_ref_aud_{i}"


def _to_r2v(graph: dict[str, Any], job: dict[str, Any] | None = None) -> None:
    """References: the base graph with the reference node in place of the image one.

    Same sampler, decoders and muxer; a different checkpoint and a node that
    takes lists. Its dynamic inputs are named `<list>.<prefix><index>` with a
    zero-based index, and the prompt refers to them one-based: <Picture 1> is
    ref_images.ref_image_0. Loaders are created per reference, in order.
    """
    job = job or {}
    h3_id, h3 = _h3_node(graph)
    link = h3["inputs"].pop("first_frame", None)
    h3["inputs"].pop("last_frame", None)
    if is_link(link):
        graph.pop(str(link[0]), None)
    h3["class_type"] = R2V_NODE
    h3["_meta"] = {"title": "MiniMax H3 Reference To Video"}
    h3["inputs"]["audio_vae"] = _audio_vae_link(graph)
    # 'match' scales references to the render size; 'max' keeps a 2048 short edge
    # for identity at the cost of every sampling step carrying the extra tokens.
    h3["inputs"]["ref_image_size"] = "match"
    for i, name in enumerate(job.get("ref_images") or []):
        loader = REF_IMAGE_ID.format(i=i)
        graph[loader] = {"class_type": "LoadImage", "_meta": {"title": f"<Picture {i + 1}>"},
                         "inputs": {"image": Path(str(name)).name}}
        h3["inputs"][f"ref_images.ref_image_{i}"] = [loader, 0]
    videos = job.get("ref_video_names") or job.get("ref_videos") or []
    for i, name in enumerate(videos):
        loader, parts = REF_VIDEO_ID.format(i=i), REF_VIDEO_PARTS_ID.format(i=i)
        graph[loader] = {"class_type": "LoadVideo", "_meta": {"title": f"<Video {i + 1}>"},
                         "inputs": {"file": Path(str(name)).name}}
        graph[parts] = {"class_type": "GetVideoComponents", "_meta": {"title": f"<Video {i + 1}> parts"},
                        "inputs": {"video": [loader, 0]}}
        h3["inputs"][f"ref_videos.ref_video_{i}"] = [parts, 0]
        h3["inputs"][f"ref_video_audios.ref_video_audio_{i}"] = [parts, 1]
    audios = job.get("ref_audio_names") or job.get("ref_audios") or []
    for i, name in enumerate(audios):
        loader = REF_AUDIO_ID.format(i=i)
        graph[loader] = {"class_type": "LoadAudio", "_meta": {"title": f"<Audio {i + 1}>"},
                         "inputs": {"audio": Path(str(name)).name}}
        h3["inputs"][f"ref_audios.ref_audio_{i}"] = [loader, 0]
    for node in graph.values():
        if isinstance(node, dict) and node.get("class_type") == "UNETLoader":
            node["inputs"]["unet_name"] = REF2VA_MODEL


#: How each mode without its own export is built from the base graph.
DERIVE = {"t2v": _to_t2v, "flf2v": _to_flf2v, "extend": _to_extend, "r2v": _to_r2v}


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


def _load_template(mode: str, job: dict[str, Any] | None = None) -> dict[str, Any]:
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
        derive(graph, job)
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


_SHOT_LABEL = re.compile(r"\[Shot\s+(\d+)\]")

I2VA_LINE = ("For the target video, at 0.00 seconds into the target video, "
             "<Picture 1> (from [Shot 1]) is fully referenced.")
FL2VA_LINE = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) "
              "aligns with the 0.00-second mark of the target video; Picture 2 (from Shot {n}) "
              "aligns with the {s:.2f}-second mark of the target video.")
L2VA_LINE = ("How the reference pictures align with the target video — <Picture 1> (from [Shot {n}]) "
             "aligns with the {s:.2f}-second mark of the target video.")


def _final_shot(description: str) -> int:
    nums = [int(n) for n in _SHOT_LABEL.findall(description)]
    return max(nums) if nums else 1


def assemble_prompt(job: dict[str, Any]) -> str:
    """The prompt the model reads, in the format MiniMax's own rewriter produces.

    ComfyUI hands the model raw text (after the `<Picture i>:` vision blocks), so
    the structure has to be written here: the alignment instruction the mode
    calls for, then `integrated_multimodal_description` opening on `[Shot 1]`,
    then the two sound fields under their official names. A field the person
    left empty is left out - `N/A` would ask for silence. References mode keeps
    the person's own tagged text, which is what its nodes expect.
    """
    mode = job.get("mode") or DEFAULT_MODE
    body = str(job.get("prompt", "")).strip()
    tokens = " ".join(effects_mod.prompt_token(e) for e in effects_mod.clean(job.get("effects")))
    if tokens:
        body = f"{tokens} {body}".strip()
    if mode == "r2v":
        parts = [body]
    else:
        description = body if body.startswith("[Shot") else f"[Shot 1] {body}".rstrip()
        refs = job.get("ref_images") or []
        seconds = float(max(4, min(15, int(job.get("seconds") or 10))))
        parts = []
        if mode == "i2v" and refs:
            parts.append(I2VA_LINE)
        elif mode == "flf2v" and len(refs) >= 2:
            parts.append(FL2VA_LINE.format(n=_final_shot(description), s=seconds))
        elif mode == "extend" and len(refs) >= 2:
            # The tail of the source clip is a guide, not a picture; an arrival
            # image is the only picture the model sees, and it is the last frame.
            parts.append(L2VA_LINE.format(n=_final_shot(description), s=seconds))
        parts.append(f"integrated_multimodal_description: {description}")
    if job.get("keep_audio") is not False:
        if job.get("sound"):
            parts.append(f"overall_soundscape: {str(job['sound']).strip()}")
        if job.get("music"):
            parts.append(f"non_diegetic_music: {str(job['music']).strip()}")
    return "\n\n".join(p for p in parts if p)

UPSCALE_MODEL = "RealESRGAN_x2.pth"
# Frames per upscale node. The upscaler writes its whole output batch to CPU
# memory at once; 32 frames at 2688x1536 is about 1.6 GB, a clip's worth is
# not something to hold on a rented box in one piece.
UPSCALE_CHUNK = 32


def upscale_graph(job: dict[str, Any]) -> dict[str, Any]:
    """Twice the size, frame by frame, with the soundtrack carried across.

    Not derived from the H3 template: no diffusion runs. The clip is loaded,
    cut into chunks, each chunk goes through the upscale model, and the chunks
    are joined back in a balanced tree - a chain would keep every partial join
    in memory at once, which is quadratic in the number of chunks.
    """
    refs = job.get("ref_images") or []
    if not refs:
        raise WorkflowError("an upscale needs the clip it enlarges")
    source = Path(str(refs[0])).name
    graph: dict[str, Any] = {
        "u_load": {"class_type": "LoadVideo", "_meta": {"title": "Load Video (the clip to upscale)"},
                   "inputs": {"file": source}},
        "u_parts": {"class_type": "GetVideoComponents", "_meta": {"title": "Get Video Components"},
                    "inputs": {"video": ["u_load", 0]}},
        "u_model": {"class_type": "UpscaleModelLoader", "_meta": {"title": "Load Upscale Model"},
                    "inputs": {"model_name": UPSCALE_MODEL}},
    }
    frames = int(job.get("source_frames") or 0)
    chunks: list[str] = []
    if frames > UPSCALE_CHUNK:
        for i, start in enumerate(range(0, frames, UPSCALE_CHUNK)):
            graph[f"u_slice_{i}"] = {
                "class_type": "ImageFromBatch", "_meta": {"title": f"Frames {start}+"},
                "inputs": {"image": ["u_parts", 0], "batch_index": start,
                           "length": min(UPSCALE_CHUNK, frames - start)}}
            graph[f"u_up_{i}"] = {
                "class_type": "ImageUpscaleWithModel", "_meta": {"title": f"Upscale chunk {i}"},
                "inputs": {"upscale_model": ["u_model", 0], "image": [f"u_slice_{i}", 0]}}
            chunks.append(f"u_up_{i}")
    else:
        # Short, or a frame count nobody measured: one pass over the whole clip.
        graph["u_up_0"] = {
            "class_type": "ImageUpscaleWithModel", "_meta": {"title": "Upscale"},
            "inputs": {"upscale_model": ["u_model", 0], "image": ["u_parts", 0]}}
        chunks.append("u_up_0")
    # Join pairwise, level by level: log2(n) joins deep instead of n.
    level, n = chunks, 0
    while len(level) > 1:
        joined = []
        for a, b in zip(level[0::2], level[1::2]):
            nid = f"u_cat_{n}"; n += 1
            graph[nid] = {"class_type": "ImageBatch", "_meta": {"title": "Join frames"},
                          "inputs": {"image1": [a, 0], "image2": [b, 0]}}
            joined.append(nid)
        if len(level) % 2:
            joined.append(level[-1])
        level = joined
    graph["u_video"] = {"class_type": "CreateVideo", "_meta": {"title": "Create Video"},
                        "inputs": {"images": [level[0], 0], "fps": ["u_parts", 2],
                                   "audio": ["u_parts", 1]}}
    graph["u_save"] = {"class_type": "SaveVideo", "_meta": {"title": "Save Video"},
                       "inputs": {"video": ["u_video", 0], "filename_prefix": "video/H3_upscale",
                                  "format": "auto", "codec": "auto"}}
    return graph


def build_workflow(job: dict[str, Any], cfg: Any,
                   features: frozenset[str] | set[str] = frozenset()) -> dict[str, Any]:
    """Patch a job's values into the exported ComfyUI template.

    `features` names what the pod that will run this graph has beyond stock
    ComfyUI - today only "sage" - so an optional node is never sent to a pod
    that cannot load it.
    """
    mode = job.get("mode") or DEFAULT_MODE
    if mode == "upscale":
        return upscale_graph(job)
    graph = _load_template(mode, job)
    preset = controls.effective(cfg.generation.preset(job.get("preset")), job)
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

    put("prompt", assemble_prompt(job)[:4000])
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

    if job.get("width") and job.get("height"):
        _set_canvas(graph, int(job["width"]), int(job["height"]))

    if job.get("audio_key"):
        _add_audio_guide(graph, str(job.get("audio_name") or job["audio_key"]))

    if job.get("keyframes"):
        _add_keyframes(graph, job["keyframes"], seconds)

    if getattr(preset, "lora", ""):
        lora = R2V_LORA.get(preset.lora, preset.lora) if mode == "r2v" else preset.lora
        _apply_lora(graph, lora, getattr(preset, "lora_strength", 1.0))

    # After the LoRA, so the chain reads model -> LoRA -> shift -> consumers.
    shift = controls.shifts(job)
    if shift is not None:
        _apply_shift(graph, *shift)

    # Last in the chain: attention is patched on whatever model the sampler reads.
    if "sage" in features and getattr(getattr(cfg, "generation", None), "sage_attention", False):
        _apply_sage(graph)

    return graph


SAGE_NODE_ID = "h3_sage"
SAGE_NODE = "PatchSageAttentionKJ"


def _model_source(graph: dict[str, Any]) -> str | None:
    """The node the sampler's consumers currently read their model from."""
    for candidate in (SHIFT_NODE_ID, LORA_NODE_ID):
        if candidate in graph:
            return candidate
    return next((nid for nid, n in graph.items()
                 if isinstance(n, dict) and n.get("class_type") == "UNETLoader"), None)


def _apply_sage(graph: dict[str, Any]) -> bool:
    """Splice the SageAttention patch in front of every consumer of the model."""
    source = _model_source(graph)
    if source is None or SAGE_NODE_ID in graph:
        return False
    graph[SAGE_NODE_ID] = {
        "class_type": SAGE_NODE,
        "_meta": {"title": "Sage Attention"},
        "inputs": {"model": [source, 0], "sage_attention": "auto"},
    }
    for nid, node in graph.items():
        if nid == SAGE_NODE_ID or not isinstance(node, dict):
            continue
        for field, value in (node.get("inputs") or {}).items():
            if field == "model" and is_link(value) and value[0] == source:
                node["inputs"][field] = [SAGE_NODE_ID, 0]
    return True


SHIFT_NODE_ID = "h3_sigma_shift"
KEYFRAME_LOADER_ID = "h3_key_{i}"
KEYFRAME_GUIDE_ID = "h3_key_guide_{i}"
FPS = 24


def frame_count(seconds: float) -> int:
    """Frames at 24 fps on the model's 17k+5 grid - the template's own arithmetic."""
    n = max(5, round(seconds * FPS))
    return n + (5 - n % 17) % 17


AUDIO_LOADER_ID = "h3_voice"
AUDIO_GUIDE_ID = "h3_voice_guide"


def _guider(graph: dict[str, Any]) -> dict[str, Any]:
    guider = next((n for n in graph.values()
                   if isinstance(n, dict) and n.get("class_type") == "BasicGuider"), None)
    if guider is None:
        raise WorkflowError("the template has no BasicGuider to hang a guide on")
    return guider


def _chain_guide(graph: dict[str, Any], guide_id: str, node: dict[str, Any]) -> None:
    """Insert a guide between whatever feeds the guider today and the guider.

    Guides accumulate on the conditioning, so the order is immaterial; what
    matters is that the guider ends up reading the last one.
    """
    guider = _guider(graph)
    node["inputs"]["positive"] = list(guider["inputs"]["conditioning"])
    graph[guide_id] = node
    guider["inputs"]["conditioning"] = [guide_id, 0]


def _add_audio_guide(graph: dict[str, Any], name: str) -> None:
    """A voice or audio track anchored at frame 0: the clip follows it."""
    h3_id, _ = _h3_node(graph)
    graph[AUDIO_LOADER_ID] = {
        "class_type": "LoadAudio",
        "_meta": {"title": "Load Audio (the track the clip follows)"},
        "inputs": {"audio": Path(name).name},
    }
    _chain_guide(graph, AUDIO_GUIDE_ID, {
        "class_type": "MiniMaxH3AddGuide",
        "_meta": {"title": "Anchor the audio track"},
        "inputs": {"latent": [h3_id, 1], "audio_vae": _audio_vae_link(graph),
                   "audio": [AUDIO_LOADER_ID, 0], "frame_idx": 0},
    })


def _add_keyframes(graph: dict[str, Any], keyframes: list[dict[str, Any]],
                   seconds: float) -> None:
    """Pin an image at a moment inside the clip, one guide per keyframe.

    Each guide reads the conditioning of the one before and hands it on, so the
    node that consumed the conditioning - the guider - now reads the last guide.
    The first and last frame are never touched: they belong to the model node.
    """
    h3_id, h3 = _h3_node(graph)
    total = frame_count(seconds)
    video_vae = h3["inputs"]["vae"]
    for i, kf in enumerate(sorted(keyframes, key=lambda k: float(k["at"]))):
        loader, guide = KEYFRAME_LOADER_ID.format(i=i), KEYFRAME_GUIDE_ID.format(i=i)
        # Strictly inside: the frame after the first, the frame before the last.
        idx = max(1, min(total - 2, round(float(kf["at"]) * FPS)))
        graph[loader] = {
            "class_type": "LoadImage",
            "_meta": {"title": f"Load Image (keyframe at {float(kf['at']):g}s)"},
            "inputs": {"image": Path(str(kf.get("name") or kf["key"])).name},
        }
        _chain_guide(graph, guide, {
            "class_type": "MiniMaxH3AddGuide",
            "_meta": {"title": f"Anchor keyframe {i + 1} at frame {idx}"},
            "inputs": {"latent": [h3_id, 1], "vae": video_vae,
                       "image": [loader, 0], "frame_idx": idx},
        })


def _apply_shift(graph: dict[str, Any], shift_video: float, shift_audio: float) -> bool:
    """Splice MiniMaxH3SigmaShift in front of every consumer of the model.

    The model's own defaults are 12 (video) and 3 (audio); the node exists only
    when a clip asks for something else, so the default graph is untouched.
    """
    unet = next((nid for nid, n in graph.items()
                 if isinstance(n, dict) and n.get("class_type") == "UNETLoader"), None)
    if unet is None:
        return False
    # The LoRA, when present, is the current model source.
    source = LORA_NODE_ID if LORA_NODE_ID in graph else unet
    graph[SHIFT_NODE_ID] = {
        "class_type": "MiniMaxH3SigmaShift",
        "_meta": {"title": "Motion (sigma shift)"},
        "inputs": {"model": [source, 0], "shift_video": float(shift_video),
                   "shift_audio": float(shift_audio)},
    }
    for nid, node in graph.items():
        if nid == SHIFT_NODE_ID or not isinstance(node, dict):
            continue
        for field, value in (node.get("inputs") or {}).items():
            if field == "model" and is_link(value) and value[0] == source:
                node["inputs"][field] = [SHIFT_NODE_ID, 0]
    return True


def _set_canvas(graph: dict[str, Any], width: int, height: int) -> None:
    """An exact render size: literals on the H3 node instead of the selector's links.

    The selector then feeds nothing and is pruned; the reference scaler is told the
    new area so the first frame is not upsampled from a smaller intermediate.
    """
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == H3_NODE:
            node["inputs"]["width"] = int(width)
            node["inputs"]["height"] = int(height)
        elif node.get("class_type") == "ImageScaleToTotalPixels" \
                and not is_link((node.get("inputs") or {}).get("megapixels")):
            node["inputs"]["megapixels"] = round(width * height / 1_000_000, 2)
    _prune_unreachable(graph)


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
