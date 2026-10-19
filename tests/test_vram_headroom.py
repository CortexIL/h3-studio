"""What ComfyUI is told about memory when the pod starts it.

A 15-second clip at 1344x768 failed on a 32 GB RTX 5090 with "Requested: 5.56
GiB ... Free (according to CUDA): 388.25 MiB". ComfyUI's DynamicVRAM had filled
the card with weights down to its default ~400 MB reserve, so the one large
allocation a long clip makes had nowhere to go.
"""
from __future__ import annotations

import shlex

from app.backends.runpod_pod import _bootstrap_cmd
from app.config import Config


def _comfy_args() -> list[str]:
    """The arguments as the image's /start.sh hands them to ComfyUI: every line
    of the args file joined, then split on whitespace."""
    script = "\n".join(_bootstrap_cmd(Config()))
    written = [shlex.split(ln)[1] for ln in script.split("\n")
               if ln.lstrip().startswith("echo") and ln.rstrip().endswith("$ARGS")]
    return " ".join(written).split()


def test_comfyui_keeps_room_for_the_largest_allocation_a_long_clip_makes():
    args = _comfy_args()
    assert "--vram-headroom" in args
    # More than the single 5.56 GiB request that ran out of room.
    assert float(args[args.index("--vram-headroom") + 1]) > 5.56


def test_the_model_paths_still_reach_comfyui():
    assert "--extra-model-paths-config" in _comfy_args()
