"""One real pod, end to end, then torn down. Costs cents.

Answers the three questions the free checks cannot:
  1. does pod creation actually work against this account
  2. do the weights download and does ComfyUI come up
  3. are the MiniMax H3 nodes really there, with the input names the workflow builder
     expects

Teardown is in a finally, and the pod list is re-checked afterwards, because a pod
surviving this script is the only way it can cost more than a few cents.

    python -m app.smoketest                  cheapest configured GPU
    python -m app.smoketest --gpu "NVIDIA L40S"
    python -m app.smoketest --keep           leave it up (for exporting templates)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import httpx

from . import config as config_mod
from .settings import get_settings
from .backends.comfy import ComfyClient
from .backends.runpod_pod import API, FALLBACK_RATES, RunpodBackend
from .doctor import H3_NODES

REPORT = Path(__file__).resolve().parent.parent / "data" / "smoketest.txt"


def log(lines: list[str], msg: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)
    lines.append(f"[{stamp}] {msg}")


async def survivors(cfg: config_mod.Config) -> list[str]:
    """Pods still alive on the account - the thing that must be zero at the end."""
    try:
        async with httpx.AsyncClient(
            timeout=30, headers={"Authorization": f"Bearer {cfg.runpod.api_key}"}
        ) as http:
            r = await http.get(f"{API}/pods")
        if r.status_code != 200:
            return [f"(could not check: HTTP {r.status_code})"]
        data = r.json()
        pods = data if isinstance(data, list) else data.get("data", [])
        return [str(p.get("id")) for p in pods]
    except httpx.HTTPError as e:
        return [f"(could not check: {e})"]


async def amain() -> int:
    ap = argparse.ArgumentParser(prog="app.smoketest")
    ap.add_argument("--gpu", default=None,
                    help="force one GPU; default is to walk runpod.gpu_preference "
                         "cheapest-first until one has capacity")
    ap.add_argument("--secure", action="store_true",
                    help="use Secure Cloud (dearer, usually available) if Community "
                         "has nothing free")
    ap.add_argument("--keep", action="store_true",
                    help="leave the pod running (remember to stop it yourself)")
    ap.add_argument("--budget", type=float, default=1.0,
                    help="abort if the run would exceed this many dollars")
    args = ap.parse_args()

    cfg = config_mod.Config.from_settings(get_settings())
    if args.gpu:
        cfg.runpod.gpu_preference = [args.gpu]
    else:
        # Cheapest first: this run only needs to prove the stack works, so paying
        # more for speed would be spending money to learn nothing extra.
        cfg.runpod.gpu_preference = sorted(
            cfg.runpod.gpu_preference, key=lambda g: FALLBACK_RATES.get(g, 99))
    if args.secure:
        cfg.runpod.cloud_type = "SECURE"
    lines: list[str] = []
    ok = False

    log(lines, f"image  : {cfg.runpod.image}")
    log(lines, f"gpus   : {cfg.runpod.gpu_preference} on {cfg.runpod.cloud_type}")
    log(lines, f"budget : ${args.budget:.2f}")

    backend = RunpodBackend(cfg)
    t0 = time.time()
    try:
        log(lines, "creating pod...")
        await backend._create()
        log(lines, f"pod created: {backend._pod_id} on {backend.gpu_used} "
                   f"@ ${backend.rate_per_hour:.2f}/hr")
        log(lines, f"endpoint: {backend._endpoint()}")

        log(lines, f"waiting for ComfyUI "
                   f"(downloads ~{cfg.weights.total_gb_hint():.0f}GB of weights first)...")
        # Report on detail changes, not just state changes: the whole download runs
        # inside a single "booting" state, so keying on state alone gives ten silent
        # minutes - which is exactly what made the earlier failures so hard to read.
        last = ""
        deadline = time.time() + cfg.pod.boot_timeout_minutes * 60
        while time.time() < deadline:
            st = await backend.status()
            marker = f"{st.state}|{st.detail}"
            if marker != last:
                log(lines, f"  [{(time.time()-t0)/60:4.1f}m] {st.state}: {st.detail}")
                last = marker
            if st.state == "ready":
                break
            if st.state in {"off", "error"}:
                log(lines, f"FAILED: {st.detail}")
                return 1
            if backend.cost_so_far() > args.budget:
                log(lines, f"ABORT: spent ${backend.cost_so_far():.2f}, over budget")
                return 1
            await asyncio.sleep(10)
        else:
            log(lines, "FAILED: timed out waiting for ComfyUI")
            return 1

        boot_min = (time.time() - t0) / 60
        log(lines, f"ComfyUI answered after {boot_min:.1f} min "
                   f"(${backend.cost_so_far():.3f} so far)")

        log(lines, "checking MiniMax H3 nodes...")
        comfy = ComfyClient(backend._endpoint() or "")
        try:
            info = await comfy.object_info()
            missing = [n for n in H3_NODES if n not in info]
            for node in H3_NODES:
                if node in info:
                    req = list((info[node].get("input", {}).get("required") or {}).keys())
                    log(lines, f"  OK   {node}({', '.join(req)})")
                else:
                    log(lines, f"  MISS {node}")
            ok = not missing
            if missing:
                log(lines, f"FAILED: {len(missing)} H3 node(s) missing - the image's "
                           f"ComfyUI is probably older than 0.30.0")

            # What ComfyUI can actually see, by the exact name a workflow must use.
            # A name carrying a subfolder prefix means the weights were unpacked one
            # level too deep: they load, but no template default matches them and the
            # graph opens with a wall of missing-model errors.
            log(lines, "models visible to ComfyUI:")
            nested = []
            for loader, field in (("UNETLoader", "unet_name"), ("CLIPLoader", "clip_name"),
                                  ("VAELoader", "vae_name"),
                                  ("LoraLoaderModelOnly", "lora_name")):
                spec = (info.get(loader, {}).get("input", {})
                        .get("required", {}).get(field))
                names = spec[0] if isinstance(spec, list) and spec else []
                for n in names:
                    if n == "pixel_space":
                        continue
                    log(lines, f"  {loader:<20} {n}")
                    if "/" in n:
                        nested.append(n)
            if nested:
                log(lines, f"WARNING: {len(nested)} model name(s) carry a folder prefix "
                           f"- weights unpacked too deep, templates will not match them")
        finally:
            await comfy.aclose()

        if args.keep:
            log(lines, "")
            log(lines, "LEAVING POD UP as requested. Open ComfyUI at:")
            log(lines, f"  {backend._endpoint()}")
            log(lines, "Export Templates > MiniMax H3 (T2V) > Workflow > Export (API)")
            log(lines, f"STOP IT when done - it bills ${backend.rate_per_hour:.2f}/hr:")
            log(lines, "  python -m app.killpods")
        return 0 if ok else 1

    except Exception as e:
        log(lines, f"FAILED: {type(e).__name__}: {e}")
        return 1
    finally:
        cost = backend.cost_so_far()
        if not args.keep:
            log(lines, "terminating pod...")
            try:
                await backend.shutdown()
                log(lines, "  terminated.")
            except Exception as e:
                log(lines, f"  !! COULD NOT TERMINATE: {e}")
                log(lines, "  !! Kill it by hand at https://console.runpod.io/pods")
        await backend.aclose()
        log(lines, f"elapsed {(time.time()-t0)/60:.1f} min, cost ~${cost:.3f}")

        left = await survivors(cfg)
        log(lines, f"pods still on the account: {left or 'none'}")
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
