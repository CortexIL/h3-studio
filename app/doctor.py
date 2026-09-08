"""Pre-flight checks. Free to run - it never starts a pod.

Run this before the first real batch. Every check here corresponds to something that
would otherwise fail *after* you have started paying for a GPU:

    python -m app.doctor                 config, API key, GPU availability and prices
    python -m app.doctor --dump-nodes    the H3 node signatures from a running pod

--dump-nodes needs a pod already up (start one with the app, or pass --endpoint) and
is how you resolve a workflow mismatch definitively rather than by guessing.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from . import config as config_mod
from .backends.comfy import ComfyClient
from .backends.runpod_pod import API, FALLBACK_RATES

OK, BAD, WARN = "  ok  ", " FAIL ", " warn "

# RunPod's REST API has no endpoint for listing GPU types - the names live in the
# OpenAPI schema as an enum on gpuTypeIds. Validating against that is the check that
# actually matters: a name outside the enum means pod creation fails at the worst
# possible moment, after the user has committed to a batch.
OPENAPI_URL = "https://rest.runpod.io/v1/openapi.json"

# Used when the schema cannot be fetched. Verified against the enum on 2026-09-08.
KNOWN_GPU_IDS = {
    "NVIDIA GeForce RTX 5090", "NVIDIA GeForce RTX 4090", "NVIDIA GeForce RTX 5080",
    "NVIDIA L40S", "NVIDIA L40", "NVIDIA L4",
    "NVIDIA RTX A6000", "NVIDIA RTX A5000", "NVIDIA RTX A4500", "NVIDIA RTX A4000",
    "NVIDIA RTX 6000 Ada Generation", "NVIDIA RTX 5000 Ada Generation",
    "NVIDIA H100 PCIe", "NVIDIA H100 NVL", "NVIDIA H100 80GB HBM3",
    "NVIDIA H200", "NVIDIA H200 NVL", "NVIDIA B200",
    "NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-80GB", "NVIDIA A100-SXM4-40GB",
    "NVIDIA A40",
}

H3_NODES = [
    "EmptyMiniMaxH3LatentAV",
    "MiniMaxH3ImageToVideo",
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3SigmaShift",
]


def line(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}")


async def check_account(cfg: config_mod.Config) -> bool:
    key = cfg.runpod.api_key
    if not key or key.startswith("YOUR_"):
        line(BAD, "no RunPod API key. Set runpod.api_key in config.yaml or RUNPOD_API_KEY.")
        return False
    async with httpx.AsyncClient(
        timeout=30, headers={"Authorization": f"Bearer {key}"}
    ) as http:
        try:
            r = await http.get(f"{API}/pods")
        except httpx.HTTPError as e:
            line(BAD, f"cannot reach RunPod: {e}")
            return False
        if r.status_code == 401:
            line(BAD, "RunPod rejected the API key (401).")
            return False
        if r.status_code >= 400:
            line(WARN, f"GET /pods returned {r.status_code}: {r.text[:200]}")
        else:
            pods = r.json()
            running = pods if isinstance(pods, list) else pods.get("data", [])
            line(OK, f"API key works. {len(running)} pod(s) currently on your account.")
            for p in running:
                line(WARN, f"pod still running: {p.get('id')} - that is costing money now")

    await check_gpu_names(cfg)
    await check_image(cfg)
    await check_pod_body(cfg)
    await check_weights(cfg)
    return True


async def check_weights(cfg: config_mod.Config) -> None:
    """Confirm every configured weight file actually exists in the HF repo.

    Added after a real pod burned 15 minutes doing nothing: the paths were wrong, the
    first download failed, `set -eu` killed the bootstrap, and ComfyUI never started -
    which from the outside looks identical to a slow download. Checking the manifest
    costs one request and nothing at all in GPU time.
    """
    repo = cfg.weights.repo
    try:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as http:
            r = await http.get(f"https://huggingface.co/api/models/{repo}?blobs=true")
    except httpx.HTTPError as e:
        line(WARN, f"could not reach HuggingFace to check weights: {e}")
        return
    if r.status_code == 404:
        line(BAD, f"weights repo {repo!r} does not exist on HuggingFace")
        return
    if r.status_code != 200:
        line(WARN, f"HuggingFace returned {r.status_code} for {repo}")
        return

    sizes = {s["rfilename"]: (s.get("size") or 0) for s in r.json().get("siblings", [])}
    total, bad = 0, 0
    for f in cfg.weights.files:
        size = sizes.get(f.src)
        if size is None:
            line(BAD, f"weight file not in repo: {f.src}")
            bad += 1
        else:
            total += size
    if not bad:
        gb = total / 1e9
        line(OK, f"all {len(cfg.weights.files)} weight files exist ({gb:.1f}GB per session)")
        disk = cfg.runpod.container_disk_gb
        if disk < gb + 25:
            line(BAD, f"container_disk_gb is {disk} but the weights alone are {gb:.0f}GB. "
                      f"Raise it to at least {int(gb) + 30}.")
        else:
            line(OK, f"container disk {disk}GB leaves {disk - gb:.0f}GB "
                     f"for the image and outputs")


async def check_pod_body(cfg: config_mod.Config) -> None:
    """Type-check the POST /pods payload against RunPod's published schema.

    Added after a real run failed on `dockerStartCmd: got string, want array`. RunPod
    validates server-side and charges nothing for a rejection, so that failure was
    cheap - but it costs a round trip and only ever reveals one bad field at a time.
    Checking the whole body here surfaces them all at once, for free.
    """
    from .backends.runpod_pod import RunpodBackend

    gpu = cfg.runpod.gpu_preference[0] if cfg.runpod.gpu_preference else "NVIDIA L40S"
    backend = RunpodBackend(cfg)
    try:
        body = backend.build_create_body(gpu)
    finally:
        await backend.aclose()

    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(OPENAPI_URL)
        spec = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        spec = None
    if not spec:
        line(WARN, "could not fetch RunPod's schema to check the pod request body")
        return

    props = _find_pod_create_props(spec)
    if not props:
        line(WARN, "could not locate PodCreateInput in RunPod's schema")
        return

    want = {"string": str, "integer": int, "number": (int, float),
            "boolean": bool, "array": list, "object": dict}
    bad = 0
    for key, value in body.items():
        schema = props.get(key)
        if schema is None:
            line(BAD, f"pod body sends {key!r}, which is not in RunPod's schema")
            bad += 1
            continue
        declared = schema.get("type")
        expected = want.get(declared)
        # bool is a subclass of int in Python; integer fields must not accept True.
        if expected and (not isinstance(value, expected)
                         or (declared == "integer" and isinstance(value, bool))):
            line(BAD, f"{key}: sending {type(value).__name__}, schema wants {declared}")
            bad += 1
            continue
        enum = schema.get("enum")
        if enum and value not in enum:
            line(BAD, f"{key}: {value!r} is not one of {enum}")
            bad += 1
    if not bad:
        line(OK, f"pod request body matches the schema ({len(body)} fields checked)")


def _find_pod_create_props(spec: dict) -> dict | None:
    schemas = (spec.get("components") or {}).get("schemas") or {}
    for name, schema in schemas.items():
        props = (schema or {}).get("properties") or {}
        if "gpuTypeIds" in props and "imageName" in props and "dockerStartCmd" in props:
            return props
    return None


async def check_image(cfg: config_mod.Config) -> None:
    """Confirm the container image tag exists.

    Worth a network call: a tag that does not exist still lets pod creation
    succeed, then the container never starts, and the failure surfaces minutes
    later as a boot timeout with nothing pointing at the real cause.
    """
    ref = cfg.runpod.image
    if ":" not in ref:
        line(WARN, f"image {ref!r} has no tag - it will resolve to :latest")
        return
    repo, tag = ref.rsplit(":", 1)
    if repo.count("/") != 1:
        line(WARN, f"cannot check image {ref!r} (not a Docker Hub name)")
        return
    try:
        async with httpx.AsyncClient(timeout=25) as http:
            r = await http.get(f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}")
    except httpx.HTTPError as e:
        line(WARN, f"could not reach Docker Hub to check {ref}: {e}")
        return
    if r.status_code == 200:
        line(OK, f"container image exists: {ref}")
    elif r.status_code == 404:
        line(BAD, f"container image {ref} DOES NOT EXIST. The pod would start and "
                  f"then never come up. Fix runpod.image in config.yaml.")
    else:
        line(WARN, f"Docker Hub returned {r.status_code} for {ref}")


async def check_gpu_names(cfg: config_mod.Config) -> None:
    """Validate the configured GPU names against RunPod's own schema."""
    valid = set(KNOWN_GPU_IDS)
    source = "built-in list"
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(OPENAPI_URL)
        if r.status_code == 200:
            enum = _find_gpu_enum(r.json())
            if enum:
                valid, source = set(enum), "RunPod's live schema"
    except (httpx.HTTPError, ValueError):
        pass

    for name in cfg.runpod.gpu_preference:
        rate = FALLBACK_RATES.get(name)
        price = f"~${rate:.2f}/hr" if rate else "price unknown"
        if name in valid:
            line(OK, f"{name}: valid GPU id ({price})")
        else:
            line(BAD, f"{name}: NOT a RunPod GPU id - pod creation would fail. "
                      f"Fix runpod.gpu_preference in config.yaml.")
    line(OK, f"names checked against {source}. RunPod has no endpoint for live "
             f"availability, so a card can still be sold out at start time - the "
             f"app falls through the list in order.")


def _find_gpu_enum(spec: dict) -> list[str] | None:
    """Pull the gpuTypeIds enum out of the OpenAPI document, wherever it sits."""
    schemas = (spec.get("components") or {}).get("schemas") or {}
    for schema in schemas.values():
        props = (schema or {}).get("properties") or {}
        field = props.get("gpuTypeIds")
        if isinstance(field, dict):
            items = field.get("items") or {}
            enum = items.get("enum")
            if isinstance(enum, list) and enum:
                return [str(v) for v in enum]
    return None


def check_config(cfg: config_mod.Config) -> bool:
    ok = True
    out = Path(cfg.output.folder)
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".h3studio_write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        line(OK, f"output folder writable: {out}")
    except OSError as e:
        line(BAD, f"output folder not writable ({out}): {e}")
        ok = False

    if not cfg.weights.files:
        line(BAD, "weights.files is empty - the pod would start with no model.")
        ok = False
    else:
        line(OK, f"{len(cfg.weights.files)} weight file(s) configured "
                 f"(~{cfg.weights.total_gb_hint():.0f}GB to download per session)")

    if cfg.runpod.network_volume_id:
        line(WARN, "network volume set: faster boots, but it bills ~$0.07/GB/month "
                   "even while the pod is off. Only worth it if you generate most days.")
    else:
        line(OK, "no network volume - weights re-download each session (~5 min, ~5 cents)")

    if cfg.budget.session_limit_usd <= 0:
        line(BAD, "budget.session_limit_usd is 0 - nothing would stop a runaway pod.")
        ok = False
    else:
        line(OK, f"budget ceiling ${cfg.budget.session_limit_usd:.2f}, "
                 f"idle shutdown {cfg.pod.idle_shutdown_minutes} min, "
                 f"max session {cfg.pod.max_session_hours}h")

    for mode, fname in (("t2v", "h3_t2v.api.json"), ("i2v", "h3_i2v.api.json")):
        p = Path(__file__).parent / "workflows" / fname
        if p.exists():
            line(OK, f"workflow template present: {fname}")
        else:
            line(WARN, f"missing {fname} - export it from ComfyUI "
                       f"(Templates > MiniMax H3 {mode.upper()} > Export (API))")
    return ok


async def dump_nodes(endpoint: str) -> None:
    c = ComfyClient(endpoint)
    try:
        if not await c.is_alive():
            line(BAD, f"no ComfyUI at {endpoint}")
            return
        info = await c.object_info()
        for node in H3_NODES:
            if node not in info:
                line(BAD, f"{node}: NOT FOUND - ComfyUI is older than 0.30.0?")
                continue
            required = info[node].get("input", {}).get("required", {})
            line(OK, f"{node}({', '.join(required.keys())})")
    finally:
        await c.aclose()


async def amain() -> int:
    ap = argparse.ArgumentParser(prog="app.doctor")
    ap.add_argument("--dump-nodes", action="store_true",
                    help="print H3 node signatures from a live ComfyUI")
    ap.add_argument("--endpoint", default=None, help="ComfyUI URL for --dump-nodes")
    args = ap.parse_args()

    cfg = config_mod.load()
    print("\n--- config ---")
    ok = check_config(cfg)

    if args.dump_nodes:
        endpoint = args.endpoint
        if not endpoint:
            line(BAD, "--dump-nodes needs --endpoint https://<podid>-8188.proxy.runpod.net")
            return 1
        print("\n--- ComfyUI nodes ---")
        await dump_nodes(endpoint)
        return 0

    print("\n--- RunPod ---")
    ok = await check_account(cfg) and ok
    print("\n" + ("all checks passed" if ok else "some checks failed - fix before spending"))
    return 0 if ok else 1


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
