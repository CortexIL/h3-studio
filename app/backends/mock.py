"""Fake backend: exercises the whole pipeline without spending money.

Boots in seconds and "renders" via ffmpeg into a real, playable MP4, so queueing,
progress, retries, the output folder and the download path can all be verified before
a single GPU is rented.

Two Windows-specific traps are handled here, both of which cost real debugging time:
  * ffmpeg reads stdin for keyboard commands and blocks forever when stdin is an
    inherited pipe that never closes - hence -nostdin and stdin=DEVNULL.
  * drawtext needs an explicit fontfile; without fontconfig it fails outright, so we
    look for a real font and silently drop the text overlay when there is none.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from . import Backend, JobResult, PodStatus

BOOT_SECONDS = 4.0
SECONDS_PER_OUTPUT_SECOND = 0.6   # pretend rendering cost
RENDER_TIMEOUT = 90

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _font() -> str | None:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _escape_drawtext(s: str) -> str:
    """drawtext has its own escaping rules; strip the offenders rather than fight them."""
    s = s[:60]
    for ch in "\\:'\"%,[]=;":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def _run(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=RENDER_TIMEOUT)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


class MockBackend:
    name = "mock"

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._boot_at: float | None = None
        self._jobs: dict[str, dict[str, Any]] = {}
        self._n = 0

    # Mirrors the RunPod backend's readouts so the UI needs no special-casing.
    def cost_so_far(self) -> float:
        return 0.0

    @property
    def rate_per_hour(self) -> float:
        return 0.0

    @property
    def gpu_used(self) -> str:
        return "mock"

    async def status(self) -> PodStatus:
        if self._boot_at is None:
            return PodStatus(state="off")
        elapsed = time.time() - self._boot_at
        if elapsed < BOOT_SECONDS:
            return PodStatus(state="booting", pod_id="mock-pod", uptime_s=elapsed,
                             detail=f"mock boot {elapsed:.0f}/{BOOT_SECONDS:.0f}s")
        return PodStatus(state="ready", endpoint="http://mock", pod_id="mock-pod",
                         uptime_s=elapsed, detail="mock pod ready")

    async def ensure_ready(self) -> PodStatus:
        if self._boot_at is None:
            self._boot_at = time.time()
        while (st := await self.status()).state == "booting":
            await asyncio.sleep(0.5)
        return st

    async def shutdown(self) -> None:
        self._boot_at = None
        self._jobs.clear()

    async def upload_image(self, data: bytes, name: str) -> str:
        return name

    async def submit(self, job: dict[str, Any]) -> str:
        self._n += 1
        rid = f"mock-{self._n:04d}"
        self._jobs[rid] = {
            "job": job,
            "start": time.time(),
            "duration": max(2.0, job.get("seconds", 10) * SECONDS_PER_OUTPUT_SECOND),
        }
        return rid

    async def poll(self, remote_id: str) -> JobResult:
        rec = self._jobs.get(remote_id)
        if rec is None:
            return JobResult(state="failed", error="unknown mock job")
        elapsed = time.time() - rec["start"]
        if elapsed < rec["duration"]:
            return JobResult(state="running", progress=min(0.99, elapsed / rec["duration"]))
        try:
            video = await asyncio.wait_for(
                asyncio.to_thread(self._render, rec["job"]), timeout=RENDER_TIMEOUT + 10
            )
        except asyncio.TimeoutError:
            return JobResult(state="failed", error="mock render timed out")
        except Exception as e:
            return JobResult(state="failed", error=f"mock render error: {e}")
        if video is None:
            return JobResult(state="failed",
                             error="ffmpeg unavailable or failed - mock cannot render")
        return JobResult(state="done", progress=1.0, video=video,
                         filename=f"{remote_id}.mp4", meta={"mock": True})

    def _render(self, job: dict[str, Any]) -> bytes | None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        g = self.cfg.generation
        preset = g.preset(job.get("preset"))
        seconds = max(1, int(job.get("seconds") or g.default_seconds))
        w, h = preset.width, preset.height

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.mp4"
            base = [
                ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i",
                f"color=c=0x101418:s={w}x{h}:d={seconds}:r={g.fps}",
                "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
            ]
            tail = ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(out)]

            font = _font()
            if font:
                text = _escape_drawtext(job.get("prompt", "")) or "H3 Studio mock"
                fontpath = font.replace(":", r"\:")
                vf = (f"drawtext=fontfile='{fontpath}':text='{text}':fontcolor=white:"
                      f"fontsize={max(16, w // 26)}:x=(w-text_w)/2:y=(h-text_h)/2")
                if _run(base + ["-vf", vf] + tail):
                    return out.read_bytes()

            # No usable font, or drawtext failed: a plain clip still proves the
            # pipeline end to end, which is all the mock exists for.
            if _run(base + tail):
                return out.read_bytes()
            if _run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i",
                     f"testsrc=size={w}x{h}:duration={seconds}:rate={g.fps}",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]):
                return out.read_bytes()
            return None

    async def aclose(self) -> None:
        return None


_ = Backend  # documents intent: MockBackend structurally satisfies Backend
