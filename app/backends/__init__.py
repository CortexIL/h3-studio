"""Backend seam.

Two concerns, deliberately kept separate because they change independently:
  * compute lifecycle  — bring a GPU up, tear it down   (RunPod today, anything tomorrow)
  * inference          — queue work on it, fetch results (ComfyUI's HTTP API)

`Backend` bundles both so the orchestrator depends on one small interface.
Swapping RunPod for another provider means writing one new class, not touching
the queue, the UI, or the download path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

PodState = Literal["off", "booting", "ready", "stopping", "error"]


@dataclass
class PodStatus:
    state: PodState = "off"
    endpoint: str | None = None
    pod_id: str | None = None
    detail: str = ""
    # Wall-clock seconds this pod has been billable, for the cost readout.
    uptime_s: float = 0.0


@dataclass
class JobResult:
    state: Literal["pending", "running", "done", "failed"] = "pending"
    progress: float = 0.0            # 0..1 when the backend reports it
    video: bytes | None = None
    filename: str | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Backend(Protocol):
    name: str

    async def status(self) -> PodStatus: ...

    async def ensure_ready(self) -> PodStatus:
        """Boot if needed and block until the inference server answers."""

    async def shutdown(self) -> None:
        """Terminate compute. Must be safe to call when already off."""

    async def upload_image(self, data: bytes, name: str) -> str:
        """Push a reference image; returns the name the workflow should reference.

        Bytes rather than a path: the image lives in the object store, and the
        process that received the upload is not necessarily the one still running
        when the job is dispatched.
        """

    async def submit(self, job: dict[str, Any]) -> str:
        """Enqueue one generation; returns a backend-side id for polling."""

    async def poll(self, remote_id: str) -> JobResult: ...

    async def aclose(self) -> None: ...
