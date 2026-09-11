"""What H3 Studio renders, as opposed to where it runs.

Presets, the weight manifest and the boot timeouts live here in code because they
describe the model. Deployment - database, bucket, secrets, ports - comes from
the environment through `app.settings`, and the handful of values an admin can
change at runtime live in the `kv` table. Nothing in this module is written back
to disk: a container filesystem does not survive a redeploy.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WeightFile(BaseModel):
    dst: str
    src: str
    # Real size, filled in by `app.doctor` from the HuggingFace manifest, so every
    # "this will download NN GB" message stays truthful when the file list
    # changes - the figure used to be a hardcoded 40 and drifted to a lie the
    # moment the weight selection was corrected to 56GB.
    gb: float | None = None


class WeightsCfg(BaseModel):
    repo: str = "Comfy-Org/MiniMax-H3"
    # Exactly what the i2v workflow loads, plus the turbo LoRA. The names must
    # match the template's own model names - ComfyUI finds them by basename - so
    # `tests/test_weights.py` fails if a template ever asks for a file that is
    # not listed here. An empty list is not a harmless default: the pod boots
    # with no model at all, and the failure only shows up on a rented GPU.
    files: list[WeightFile] = Field(
        default_factory=lambda: [
            WeightFile(src="diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                       dst="diffusion_models", gb=20.97),
            WeightFile(src="text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
                       dst="text_encoders", gb=27.14),
            WeightFile(src="vae/minimax_h3_video_vae_fp16.safetensors", dst="vae", gb=5.21),
            WeightFile(src="vae/minimax_h3_audio_vae_fp32.safetensors", dst="vae", gb=0.61),
            WeightFile(src="loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
                       dst="loras", gb=1.96),
        ]
    )

    def total_gb_hint(self) -> float:
        known = [f.gb for f in self.files if f.gb]
        if known and len(known) == len(self.files):
            return round(sum(known), 1)
        # No cached sizes yet: assume each unmeasured file is a large one rather
        # than under-promising, since the number drives disk sizing and boot
        # timeouts.
        return round(sum(f.gb or 12.0 for f in self.files), 1)


class RunpodCfg(BaseModel):
    api_key: str = ""
    gpu_preference: list[str] = Field(
        default_factory=lambda: [
            "NVIDIA GeForce RTX 5090",
            "NVIDIA L40S",
            "NVIDIA RTX A6000",
        ]
    )
    cloud_type: str = "COMMUNITY"
    interruptible: bool = False
    image: str = "runpod/comfyui:1.4.7-cuda12.8"
    # Empty = derived from the image tag. Only override if you know a specific
    # host driver works; leaving it unset entirely lets RunPod place the pod on a
    # machine whose driver is older than the image's torch build, which fails at
    # import.
    allowed_cuda_versions: list[str] = Field(default_factory=list)
    # 0 = derive from the weight sizes. RunPod defaults this to 8GB, which is far
    # below what staging a 27GB checkpoint through RAM needs.
    min_ram_gb: int = 0
    # The weights alone are ~56GB and the ComfyUI image is tens of GB more, so
    # 80 left no room: a download that fills the disk fails as "download failed".
    container_disk_gb: int = 140
    network_volume_id: str = ""
    data_center_ids: list[str] = Field(default_factory=list)


class PodCfg(BaseModel):
    policy: str = "off"  # auto | keep-warm | off
    idle_shutdown_minutes: int = 10
    max_session_hours: int = 6
    boot_timeout_minutes: int = 35


class Preset(BaseModel):
    width: int = 768
    height: int = 432
    steps: int = 20
    # A step-distilled LoRA lets the same model reach a usable image in a handful
    # of steps instead of thirty. That ratio *is* the cost of the batch: sampling
    # here runs at ~63s/step because 20GB of weights are streamed through a 32GB
    # card every step, so 30 steps is ~33 minutes of rented GPU per clip and 4
    # steps is under five. Empty = plain sampling.
    lora: str = ""
    lora_strength: float = 1.0
    # The size a clip is delivered at, when that differs from what the model
    # renders. H3 renders only multiples of 32, so an exact 1280x720 is reached by
    # rendering at the native 16:9 size above and conforming the finished file.
    # 0 = deliver what the model rendered.
    output_width: int = 0
    output_height: int = 0


class GenerationCfg(BaseModel):
    fps: int = 24
    default_seconds: int = 10
    presets: dict[str, Preset] = Field(
        default_factory=lambda: {
            "draft": Preset(width=768, height=432, steps=20),
            "final": Preset(width=1344, height=768, steps=30),
            "turbo": Preset(
                width=1344, height=768, steps=4,
                lora="minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
            ),
            # Final quality, delivered at exactly 1280x720 16:9.
            "hd720": Preset(width=1344, height=768, steps=30,
                            output_width=1280, output_height=720),
        }
    )
    default_preset: str = "final"
    # i2v: nearly every job here starts from a reference frame, and a reference
    # silently ignored by a t2v workflow is an expensive mistake.
    default_mode: str = "i2v"

    def preset(self, name: str | None) -> Preset:
        return self.presets.get(name or self.default_preset) or Preset()


class BudgetCfg(BaseModel):
    session_limit_usd: float = 8.0
    warn_at_usd: float = 5.0


class Config(BaseModel):
    runpod: RunpodCfg = Field(default_factory=RunpodCfg)
    weights: WeightsCfg = Field(default_factory=WeightsCfg)
    pod: PodCfg = Field(default_factory=PodCfg)
    generation: GenerationCfg = Field(default_factory=GenerationCfg)
    budget: BudgetCfg = Field(default_factory=BudgetCfg)

    # Written by `python -m app.calibrate` from a real timed run, and loaded from
    # `kv` at startup. Once set, every cost figure in the UI is a measurement
    # rather than an extrapolation.
    measured_minutes_per_clip: float | None = None
    measured_on_gpu: str | None = None
    measured_at: str | None = None

    # Swaps RunPod+ComfyUI for a fake backend so the queue, UI and download path
    # can be exercised end to end without renting anything.
    mock: bool = False

    @classmethod
    def from_settings(cls, s: Any) -> "Config":
        cfg = cls()
        cfg.runpod.api_key = s.runpod_api_key
        cfg.pod.policy = s.pod_policy
        cfg.budget.session_limit_usd = s.budget_session_limit_usd
        cfg.mock = s.mock
        return cfg

    def public(self) -> dict[str, Any]:
        """The subset safe to hand the browser.

        No API key, no server paths, no budget: a user cannot act on any of them,
        and the folder layout of the host is not the browser's business.
        """
        return {
            "presets": {k: v.model_dump() for k, v in self.generation.presets.items()},
            "default_preset": self.generation.default_preset,
            "default_mode": self.generation.default_mode,
            "default_seconds": self.generation.default_seconds,
            "fps": self.generation.fps,
            "mock": self.mock,
        }
