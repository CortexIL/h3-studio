"""Batch cost estimation.

Every number here is an extrapolation from one published measurement (4x H100
rendering a 5s / 50-step / 1344x768 clip in 13.25s pipeline latency) and should be
treated as a planning aid, not a quote. `python -m app.calibrate` replaces the guess
with a measurement from your own account and saves it to the database, which is
the only way these figures become trustworthy.
"""
from __future__ import annotations

from typing import Any

# Minutes per clip at the reference point: 1344x768, 30 steps, 10 seconds.
REFERENCE_MINUTES = {
    "NVIDIA GeForce RTX 5090": 4.0,
    "NVIDIA L40S": 6.0,
    "NVIDIA RTX A6000": 12.0,
    "NVIDIA H100 PCIe": 3.0,
    "NVIDIA A100 80GB PCIe": 5.0,
}
DEFAULT_MINUTES = 6.0

RATES = {
    "NVIDIA GeForce RTX 5090": 0.99,
    "NVIDIA L40S": 0.86,
    "NVIDIA RTX A6000": 0.49,
    "NVIDIA H100 PCIe": 2.89,
    "NVIDIA A100 80GB PCIe": 1.39,
}
DEFAULT_RATE = 1.0

REF_PIXELS = 1344 * 768
REF_STEPS = 30
REF_SECONDS = 10

# Pod boot + weight download + model load, all paid at the GPU rate. Charged once per
# session, which is why one big batch is far cheaper than many small ones.
FIXED_BOOT_MINUTES = 3.0     # image pull, container start, ComfyUI import
DOWNLOAD_GB_PER_MINUTE = 8.0  # ~133 MB/s, a conservative figure for RunPod egress


def startup_minutes(cfg: Any = None) -> float:
    """Scale the boot estimate with the actual weight set.

    This used to be a flat 7 minutes written when the weight list was ~40GB. Correcting
    the list to 56GB silently made every estimate optimistic, so it is derived now.
    """
    gb = 40.0
    if cfg is not None:
        weights = getattr(cfg, "weights", None)
        if weights is not None:
            gb = weights.total_gb_hint()
    return round(FIXED_BOOT_MINUTES + gb / DOWNLOAD_GB_PER_MINUTE, 1)


def minutes_per_clip(gpu: str, preset: Any, seconds: int, cfg: Any = None) -> float:
    base = DEFAULT_MINUTES
    if cfg is not None:
        measured = getattr(cfg, "measured_minutes_per_clip", None)
        if measured:
            base = float(measured)
        else:
            base = REFERENCE_MINUTES.get(gpu, DEFAULT_MINUTES)
    else:
        base = REFERENCE_MINUTES.get(gpu, DEFAULT_MINUTES)

    px = max(1, preset.width * preset.height)
    return (
        base
        * (preset.steps / REF_STEPS)
        * (px / REF_PIXELS)
        * (max(4, seconds) / REF_SECONDS)
    )


def estimate_batch(gpu: str, clips: int, preset: Any, seconds: int,
                   cfg: Any = None) -> dict[str, Any]:
    rate = RATES.get(gpu, DEFAULT_RATE)
    per_clip = minutes_per_clip(gpu, preset, seconds, cfg)
    render_minutes = per_clip * clips
    boot = startup_minutes(cfg)
    total_minutes = render_minutes + boot
    cost = total_minutes / 60.0 * rate
    return {
        "gpu": gpu,
        "rate_per_hour": rate,
        "minutes_per_clip": round(per_clip, 2),
        "render_minutes": round(render_minutes, 1),
        "startup_minutes": boot,
        "total_minutes": round(total_minutes, 1),
        "cost_usd": round(cost, 2),
        "cost_per_clip_usd": round(cost / max(1, clips), 3),
        "confidence": "estimated" if not getattr(cfg, "measured_minutes_per_clip", None)
                      else "measured",
    }
