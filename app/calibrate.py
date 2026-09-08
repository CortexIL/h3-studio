"""Measure what a clip actually costs on your account, instead of trusting an estimate.

Every timing figure in this project is extrapolated from one published benchmark. That
is fine for planning and useless for budgeting. This script rents one GPU, times a real
boot and two real clips, writes the measurement into config.yaml, and terminates the pod.

    python -m app.calibrate                  # cheapest preferred GPU, ~$0.30-0.60
    python -m app.calibrate --gpu "NVIDIA L40S"

After it runs, every cost estimate in the UI switches from "estimated" to "measured".
Do this before committing to a 40 clip batch - if a 10 second clip turns out to take
20 minutes rather than 4, you want to find out for 50 cents, not 30 dollars.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import yaml

from . import config as config_mod
from .backends.runpod_pod import RunpodBackend

ROOT = Path(__file__).resolve().parent.parent


async def timed_clip(backend: RunpodBackend, prompt: str, seconds: int,
                     preset_name: str) -> tuple[float, bool, str]:
    job = {"id": "calib", "prompt": prompt, "seconds": seconds,
           "preset": preset_name, "mode": "t2v", "ref_images": []}
    t0 = time.time()
    try:
        rid = await backend.submit(job)
    except Exception as e:
        return 0.0, False, f"submit failed: {e}"
    while time.time() - t0 < 45 * 60:
        res = await backend.poll(rid)
        if res.state == "done":
            return time.time() - t0, True, ""
        if res.state == "failed":
            return time.time() - t0, False, res.error or "failed"
        await asyncio.sleep(5)
    return time.time() - t0, False, "timed out after 45 min"


async def amain() -> int:
    ap = argparse.ArgumentParser(prog="app.calibrate")
    ap.add_argument("--gpu", default=None, help="override the GPU to measure")
    ap.add_argument("--seconds", type=int, default=10, help="clip length to measure")
    ap.add_argument("--preset", default="final", help="preset to measure (default: final)")
    args = ap.parse_args()

    cfg = config_mod.load()
    if args.gpu:
        cfg.runpod.gpu_preference = [args.gpu]
    if not cfg.runpod.api_key or cfg.runpod.api_key.startswith("YOUR_"):
        print("no RunPod API key - set runpod.api_key in config.yaml first")
        return 1

    preset = cfg.generation.preset(args.preset)
    print(f"\ncalibrating: {args.seconds}s clip at {preset.width}x{preset.height}, "
          f"{preset.steps} steps")
    print("this starts a real pod and will cost roughly $0.30-0.60\n")

    backend = RunpodBackend(cfg)
    boot_s = 0.0
    try:
        print("starting pod (this includes the ~40GB weight download)...")
        t0 = time.time()
        await backend.ensure_ready()
        boot_s = time.time() - t0
        print(f"  ready in {boot_s/60:.1f} min on {backend.gpu_used} "
              f"@ ${backend.rate_per_hour:.2f}/hr\n")

        results: list[float] = []
        for i in (1, 2):
            print(f"clip {i}/2 ...", end=" ", flush=True)
            dur, ok, err = await timed_clip(
                backend, "a slow cinematic drone shot over misty mountains at dawn",
                args.seconds, args.preset)
            if not ok:
                print(f"FAILED: {err}")
                # A failure here is the whole point of calibrating cheaply.
                continue
            results.append(dur)
            print(f"{dur/60:.2f} min")

        if not results:
            print("\nno clip completed - nothing to record. The error above is what "
                  "would have broken your batch.")
            return 1

        # First clip usually includes a one-off model load into VRAM; the second is
        # the number that actually predicts a long batch.
        per_clip_min = (results[-1] if len(results) > 1 else results[0]) / 60.0

        print(f"\n  measured: {per_clip_min:.2f} min per {args.seconds}s clip")
        print(f"  cost per clip: ${per_clip_min / 60 * backend.rate_per_hour:.3f}")
        for n in (10, 35, 100):
            total = (boot_s / 60 + per_clip_min * n) / 60 * backend.rate_per_hour
            print(f"  {n:>3} clips -> ${total:.2f} "
                  f"({(boot_s/60 + per_clip_min*n)/60:.1f} hours)")

        _write_measurement(per_clip_min, args, backend, preset)
        print("\nwritten to config.yaml - the UI now shows measured, not estimated, costs.")
        return 0
    finally:
        print("\nterminating pod...")
        try:
            await backend.shutdown()
            print("  pod terminated.")
        except Exception as e:
            print(f"  !! COULD NOT TERMINATE: {e}")
            print("  !! Check https://console.runpod.io/pods and kill it by hand.")
        await backend.aclose()


def _write_measurement(per_clip_min: float, args, backend: RunpodBackend, preset) -> None:
    """Normalise to the reference point the estimator uses (1344x768, 30 steps, 10s)."""
    from .estimate import REF_PIXELS, REF_SECONDS, REF_STEPS

    factor = ((preset.steps / REF_STEPS)
              * ((preset.width * preset.height) / REF_PIXELS)
              * (max(4, args.seconds) / REF_SECONDS))
    normalised = per_clip_min / factor if factor else per_clip_min

    path = ROOT / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["measured_minutes_per_clip"] = round(normalised, 3)
    data["measured_on_gpu"] = backend.gpu_used
    data["measured_at"] = time.strftime("%Y-%m-%d %H:%M")
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
