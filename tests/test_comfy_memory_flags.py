"""What ComfyUI is told about memory when the pod starts it.

A 15-second clip at 1344x768 failed on a 32 GB RTX 5090 with "Requested: 5.56
GiB ... Free (according to CUDA): 388.25 MiB". ComfyUI's DynamicVRAM had filled
the card with weights down to its default ~400 MB reserve, so the one large
allocation a long clip makes had nowhere to go. 8 GB and 12 GB reserves then still
ran out on a 15-second Sharp clip at 768x1344 ("VRAM grow failed").
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


def test_comfyui_sizes_weights_to_the_clip_instead_of_growing_on_demand():
    """DynamicVRAM ran out mid-sampling on 15-second clips even with 12 GB kept
    free ("VRAM grow failed"), so it is off and the reserve flag with it."""
    args = _comfy_args()
    assert "--disable-dynamic-vram" in args
    assert "--vram-headroom" not in args


def test_comfyui_uses_the_allocator_that_gives_its_cache_back():
    """With DynamicVRAM off, a 15-second clip still failed on a 5090: "Currently
    allocated: 14.21 GiB, Requested: 10.82 GiB, Device limit: 31.36 GiB, Free
    (according to CUDA): 19.69 MiB". That is 25 GiB on a 31 GiB card; the other
    ~17 GiB sat in cudaMallocAsync's pool, freed but kept, and that backend does
    not release it on an OOM. PyTorch's native allocator frees its cache and
    retries. ComfyUI turns cudaMallocAsync on by default, so it is switched off."""
    args = _comfy_args()
    assert "--disable-cuda-malloc" in args
    assert "--cuda-malloc" not in args


def test_the_model_paths_still_reach_comfyui():
    assert "--extra-model-paths-config" in _comfy_args()
