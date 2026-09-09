"""Config loading. Secrets live server-side only and are never sent to the browser."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


class WeightFile(BaseModel):
    dst: str
    src: str
    # Real size, filled in by `app.doctor` from the HuggingFace manifest. Cached in
    # config.yaml so every "this will download NN GB" message stays truthful when the
    # file list changes - the figure used to be a hardcoded 40 and drifted to a lie
    # the moment the weight selection was corrected to 56GB.
    gb: float | None = None


class WeightsCfg(BaseModel):
    repo: str = "Comfy-Org/MiniMax-H3"
    files: list[WeightFile] = Field(default_factory=list)

    def total_gb_hint(self) -> float:
        known = [f.gb for f in self.files if f.gb]
        if known and len(known) == len(self.files):
            return round(sum(known), 1)
        # No cached sizes yet: assume each unmeasured file is a large one rather than
        # under-promising, since the number drives disk sizing and boot timeouts.
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
    # Empty = derived from the image tag. Only override if you know a specific host
    # driver works; leaving it unset entirely lets RunPod place the pod on a machine
    # whose driver is older than the image's torch build, which fails at import.
    allowed_cuda_versions: list[str] = Field(default_factory=list)
    # 0 = derive from the weight sizes. RunPod defaults this to 8GB, which is
    # far below what staging a 27GB checkpoint through RAM needs.
    min_ram_gb: int = 0
    container_disk_gb: int = 80
    network_volume_id: str = ""
    data_center_ids: list[str] = Field(default_factory=list)


class PodCfg(BaseModel):
    policy: str = "auto"  # auto | keep-warm | off
    idle_shutdown_minutes: int = 10
    max_session_hours: int = 6
    boot_timeout_minutes: int = 35


class Preset(BaseModel):
    width: int = 768
    height: int = 432
    steps: int = 20


class GenerationCfg(BaseModel):
    fps: int = 24
    default_seconds: int = 10
    presets: dict[str, Preset] = Field(
        default_factory=lambda: {
            "draft": Preset(width=768, height=432, steps=20),
            "final": Preset(width=1344, height=768, steps=30),
        }
    )
    default_preset: str = "final"
    # i2v: nearly every job here starts from a reference frame, and a
    # reference silently ignored by a t2v workflow is an expensive mistake.
    default_mode: str = "i2v"

    def preset(self, name: str | None) -> Preset:
        return self.presets.get(name or self.default_preset) or Preset()


class OutputCfg(BaseModel):
    sink: str = "local_folder"
    folder: str = str(ROOT / "out")
    # Watched folder: any image saved here becomes a reference image. This is how
    # other apps on the machine hand pictures to H3 Studio without integrating.
    inbox: str = str(ROOT / "inbox")


class BudgetCfg(BaseModel):
    session_limit_usd: float = 8.0
    warn_at_usd: float = 5.0


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8777


class Config(BaseModel):
    runpod: RunpodCfg = Field(default_factory=RunpodCfg)
    weights: WeightsCfg = Field(default_factory=WeightsCfg)
    pod: PodCfg = Field(default_factory=PodCfg)
    generation: GenerationCfg = Field(default_factory=GenerationCfg)
    output: OutputCfg = Field(default_factory=OutputCfg)
    budget: BudgetCfg = Field(default_factory=BudgetCfg)
    server: ServerCfg = Field(default_factory=ServerCfg)

    # Written by `python -m app.calibrate` from a real timed run. Once set, every
    # cost figure in the UI is a measurement rather than an extrapolation.
    measured_minutes_per_clip: float | None = None
    measured_on_gpu: str | None = None
    measured_at: str | None = None

    # Set by --mock: swaps RunPod+ComfyUI for a fake backend so the queue, UI and
    # download path can be exercised end to end without renting anything.
    mock: bool = False

    def public(self) -> dict[str, Any]:
        """The subset safe to hand the browser - no api_key."""
        return {
            "gpu_preference": self.runpod.gpu_preference,
            "interruptible": self.runpod.interruptible,
            "presets": {k: v.model_dump() for k, v in self.generation.presets.items()},
            "default_preset": self.generation.default_preset,
            "default_mode": self.generation.default_mode,
            "default_seconds": self.generation.default_seconds,
            "fps": self.generation.fps,
            "output_folder": self.output.folder,
            "inbox_folder": self.output.inbox,
            "idle_shutdown_minutes": self.pod.idle_shutdown_minutes,
            "max_session_hours": self.pod.max_session_hours,
            "session_limit_usd": self.budget.session_limit_usd,
            "measured_on_gpu": self.measured_on_gpu,
            "measured_at": self.measured_at,
            "mock": self.mock,
        }

    def save(self, path: Path | None = None) -> None:
        path = path or (ROOT / "config.yaml")
        data = self.model_dump(exclude={"mock"})
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load(path: Path | None = None) -> Config:
    path = path or (ROOT / "config.yaml")
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config(**raw)
    if os.environ.get("RUNPOD_API_KEY"):  # lets a server run with no config.yaml
        cfg.runpod.api_key = os.environ["RUNPOD_API_KEY"]
    return cfg
