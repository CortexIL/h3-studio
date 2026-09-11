"""Deterministic stand-ins for the GPU, the sink and the object store.

No timers, no ffmpeg, no money - which is what lets the orchestrator's policy
machine, retry cap and budget ceiling be driven directly in a test.
"""
from __future__ import annotations

from typing import Any

from app.backends import JobResult, PodStatus


class FakeBackend:
    name = "fake"

    def __init__(self, *, fail_times: int = 0, poll_state: str = "done") -> None:
        self.cfg = None
        self.up = False
        self.submitted: list[dict] = []
        self.uploaded: list[tuple[bytes, str]] = []
        self.fail_times = fail_times
        self.poll_state = poll_state
        self.shutdowns = 0
        self.cost = 0.0

    def cost_so_far(self) -> float:
        return self.cost

    @property
    def rate_per_hour(self) -> float:
        return 1.0

    @property
    def gpu_used(self) -> str:
        return "fake-gpu"

    async def status(self) -> PodStatus:
        return PodStatus(state="ready" if self.up else "off", pod_id="fake")

    async def ensure_ready(self) -> PodStatus:
        self.up = True
        return PodStatus(state="ready", pod_id="fake", endpoint="http://fake")

    async def shutdown(self) -> None:
        self.up = False
        self.shutdowns += 1

    async def upload_image(self, data: bytes, name: str) -> str:
        self.uploaded.append((data, name))
        return name

    async def submit(self, job: dict) -> str:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("submit blew up")
        self.submitted.append(job)
        return f"remote-{job['id']}"

    async def poll(self, remote_id: str) -> JobResult:
        if self.poll_state == "failed":
            return JobResult(state="failed", error="the render failed")
        return JobResult(state="done", video=b"mp4-bytes", filename="c.mp4")

    async def aclose(self) -> None:
        return None


class FakeSink:
    name = "fake"

    def __init__(self, *, explode: bool = False) -> None:
        self.saved: list[tuple[str, bytes]] = []
        self.explode = explode

    async def put(self, job: dict[str, Any], data: bytes, filename: str) -> str:
        if self.explode:
            raise RuntimeError("the object store is down")
        key = f"videos/{job['user_id']}/{job['id']}.mp4"
        self.saved.append((key, data))
        return key


class FakeStorage:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}

    async def get(self, key: str) -> bytes:
        return self.objects[key]
