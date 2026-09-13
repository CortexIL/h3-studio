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

from . import modes


class WeightFile(BaseModel):
    dst: str
    src: str
    # Another Hugging Face repo than the model's own. The path inside it must
    # still start with the ComfyUI folder the file belongs in.
    repo: str | None = None
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
            WeightFile(src="loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
                       dst="loras", gb=1.96),
            # Effect presets: community prompt embeddings, a megabyte each.
            *[WeightFile(src=f"embeddings/minimaxh3_{name}.safetensors", dst="embeddings", gb=0.001)
              for name in ("art_is_explosion", "blooming_flowers", "bullet_time", "dark_magic",
                           "fire_breath", "four_seasons", "kiss_camera", "spiral_ascent",
                           "storm_magic", "truman_show")],
            # References mode: the ref2va checkpoint and its own turbo LoRA. A
            # second 21 GB model; the pod swaps them as jobs alternate.
            WeightFile(src="diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
                       dst="diffusion_models", gb=20.97),
            WeightFile(src="loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
                       dst="loras", gb=1.96),
            # The upscaler behind "Upscale 2x": Real-ESRGAN, the same family
            # Upscayl ships, loaded by ComfyUI's own upscale nodes.
            WeightFile(repo="fofr/comfyui", src="upscale_models/RealESRGAN_x2.pth",
                       dst="upscale_models", gb=0.067),
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
    # Fastest first, then whatever else can hold the weights. A short list is
    # how a batch ends up waiting on "no instances currently available": every
    # card here is only a preference, and the first one with capacity wins.
    gpu_preference: list[str] = Field(
        default_factory=lambda: [
            "NVIDIA GeForce RTX 5090",
            "NVIDIA L40S",
            "NVIDIA RTX 6000 Ada Generation",
            "NVIDIA L40",
            "NVIDIA A40",
            "NVIDIA RTX A6000",
            "NVIDIA A100 80GB PCIe",
        ]
    )
    cloud_type: str = "COMMUNITY"
    # Tried when the primary cloud has nothing free. Secure capacity is dearer
    # but usually there, and a pod that never starts costs a batch its evening.
    # Empty disables the fallback.
    cloud_fallback: str = "SECURE"
    interruptible: bool = False
    # Must carry a ComfyUI with MiniMaxH3AddGuide, added 2026-08-13 in
    # Comfy-Org/ComfyUI e01fb4c5. The 1.4.x line pins an older ComfyUI despite
    # the higher number - 1.4.7 has the H3 nodes but not the guide, which is why
    # every extend render failed with missing_node_type. Only the tags that name
    # their ComfyUI version are safe to reason about.
    image: str = "runpod/comfyui:1.3.0-rc.164-comfyuiv0.35.0-cuda12.8"
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
    # Reachable only through an action on a finished clip, never from the
    # Quality picker.
    hidden: bool = False


class GenerationCfg(BaseModel):
    fps: int = 24
    # Patch the diffusion model's attention with SageAttention when the pod has
    # it: the same output, about a quarter less time per step. The bootstrap
    # installs it best-effort; a pod without it renders the stock way.
    sage_attention: bool = True
    default_seconds: int = 10
    presets: dict[str, Preset] = Field(
        default_factory=lambda: {
            # The picker offers only the model's native canvas, 1344x768 (a
            # 768-pixel short edge, what the weights were trained on). Sizes
            # above or below it still exist here so old rows and batch files keep
            # resolving, but they are hidden: the owner asked for native only.
            "draft": Preset(width=768, height=432, steps=20, hidden=True),
            "final": Preset(width=1344, height=768, steps=30),
            "turbo": Preset(
                width=1344, height=768, steps=4,
                lora="minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
            ),
            # The 8-step distillation: about twice Turbo's time for a picture and
            # a soundtrack closer to Final's (lightx2v runs its own studio on it).
            "balanced": Preset(
                width=1344, height=768, steps=8,
                lora="minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
            ),
            # 2.7x the trained canvas. The nodes accept it, but it is not native,
            # so it left the picker; the reliable 1080p is an enlargement pass.
            "hd1080": Preset(width=1920, height=1088, steps=30, hidden=True),
            # What "Upscale" delivers: twice the native render, or that conformed
            # to an exact 1080p. Width/height here describe the output, for the
            # estimate; no diffusion step runs.
            "up2x": Preset(width=2688, height=1536, steps=0, hidden=True),
            "hd1080up": Preset(width=2688, height=1536, steps=0,
                               output_width=1920, output_height=1080, hidden=True),
            # A delivery-size variant of Final; it left the picker (the owner asked
            # for native sizes only) but old rows still resolve.
            "hd720": Preset(width=1344, height=768, steps=30,
                            output_width=1280, output_height=720, hidden=True),
        }
    )
    default_preset: str = "turbo"
    # i2v: nearly every job here starts from a reference frame, and a reference
    # silently ignored by a t2v workflow is an expensive mistake.
    default_mode: str = modes.DEFAULT_MODE

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
    # The prompt helper's language-model key, set from the Admin page and kept in
    # kv. Never returned to a browser; never logged.
    anthropic_api_key: str = ""
    measured_on_gpu: str | None = None
    measured_at: str | None = None

    # Swaps RunPod+ComfyUI for a fake backend so the queue, UI and download path
    # can be exercised end to end without renting anything.
    mock: bool = False

    # Whether a clip keeps the sound H3 generates, when the clip itself does not
    # say. The audio is real at thirty steps and unusable noise under the turbo
    # LoRA, so this is where the composer's switch starts and each clip may
    # decide otherwise.
    keep_audio: bool = True

    @classmethod
    def from_settings(cls, s: Any) -> "Config":
        cfg = cls()
        cfg.runpod.api_key = s.runpod_api_key
        cfg.pod.policy = s.pod_policy
        cfg.budget.session_limit_usd = s.budget_session_limit_usd
        cfg.mock = s.mock
        cfg.keep_audio = s.keep_audio
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
            "keep_audio": self.keep_audio,
            "estimate": self.estimate_table(),
            "prompt_helper": bool(self.anthropic_api_key),
        }

    def estimate_table(self) -> dict[str, Any]:
        """Minutes per clip for every preset at the 10-second reference length,
        on the GPU the pod will ask for first. The picker scales these by the
        chosen length, so a person sees the waiting time of each quality before
        choosing one - the same figures the /api/estimate line uses.
        """
        from . import estimate  # noqa: PLC0415 - avoids a cycle at import time

        gpu = self.runpod.gpu_preference[0] if self.runpod.gpu_preference else ""
        return {
            "gpu": gpu,
            "confidence": "measured" if self.measured_minutes_per_clip else "estimated",
            "minutes_per_10s": {
                key: round(estimate.minutes_per_clip(gpu, preset, estimate.REF_SECONDS, self), 2)
                for key, preset in self.generation.presets.items()
            },
        }
