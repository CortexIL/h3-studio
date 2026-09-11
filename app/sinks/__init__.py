"""Where finished videos go.

One interface, one implementation, and a Storage behind it that is either a
bucket or a directory. The orchestrator hands over bytes and gets back a locator;
it has never needed to know more than that.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class OutputSink(Protocol):
    async def put(self, job: dict[str, Any], data: bytes, filename: str) -> str:
        """Persist one finished video; returns a locator (path today, URL later)."""


_SLUG_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SPACE = re.compile(r"[\s_]+", re.UNICODE)


def slugify(text: str, limit: int = 48) -> str:
    """Readable filename from a prompt. Keeps Hebrew and other non-ASCII intact."""
    s = _SLUG_STRIP.sub("", text or "").strip()
    s = _SLUG_SPACE.sub("-", s)
    return s[:limit].strip("-") or "clip"


def strip_audio_bytes(data: bytes) -> bytes:
    """Drop the audio stream, returning new bytes.

    Under a step-distilled LoRA the audio H3 emits is undenoised noise, measured
    at -13.9 dB flat against -34.8 dB for the same prompt at thirty steps.
    `-c copy` remuxes rather than re-encodes, so this costs milliseconds and
    cannot degrade the picture.

    Any failure returns the input untouched: a clip with unwanted audio is a far
    better outcome than no clip, on something the user has already paid for.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot strip audio: ffmpeg is not on PATH")
        return data
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "out.mp4"
        src.write_bytes(data)
        try:
            r = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", str(src),
                 "-map", "0:v", "-c", "copy", "-an", str(dst)],
                capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("audio strip failed: %s", e)
            return data
        # Trust the file, not the exit code: ffmpeg can return 0 having written
        # nothing usable, and replacing a good clip with an empty one is the one
        # failure that would actually lose work.
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
            log.warning("audio strip produced nothing usable; keeping the original")
            return data
        return dst.read_bytes()


def conform_bytes(data: bytes, width: int, height: int) -> bytes:
    """Scale and centre-crop a clip to exactly width x height, returning new bytes.

    H3 renders only multiples of 32, so a delivery size like 1280x720 comes from
    rendering at the model's native 16:9 size and conforming afterwards: scale
    until the frame covers the target, then crop the few rows that overhang.
    Near-lossless (CRF 16), because this is the copy people edit.

    Any failure returns the input untouched, as with the audio strip: a clip at
    the render size is a far better outcome than no clip.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot conform to %dx%d: ffmpeg is not on PATH", width, height)
        return data
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
          f"crop={width}:{height},setsar=1")
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "out.mp4"
        src.write_bytes(data)
        try:
            r = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", str(src),
                 "-map", "0:v", "-map", "0:a?", "-vf", vf,
                 "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)],
                capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("conform to %dx%d failed: %s", width, height, e)
            return data
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
            log.warning("conform to %dx%d produced nothing usable; keeping the render",
                        width, height)
            return data
        return dst.read_bytes()


def extract_poster(data: bytes, width: int = 640) -> bytes | None:
    """One JPEG frame from a clip, or None.

    Half a second in rather than frame zero, which is often a black or
    half-formed first frame. Falls back to frame zero for clips shorter than
    that. Any failure returns None: a clip without a poster is fine, a clip
    that failed because its poster did would not be.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "poster.jpg"
        src.write_bytes(data)
        for seek in ("0.5", "0"):
            try:
                subprocess.run(
                    [ffmpeg, "-v", "error", "-y", "-ss", seek, "-i", str(src),
                     "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)],
                    capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                return None
            if dst.exists() and dst.stat().st_size > 0:
                out = dst.read_bytes()
                return out if out[:2] == b"\xff\xd8" else None
    return None


def make_sink(settings: Any, generation: Any = None) -> OutputSink:
    """Where finished clips go. One implementation; the storage behind it varies."""
    from .. import storage as storage_mod
    from .object_store import ObjectSink
    return ObjectSink(storage_mod.get_storage(), keep_audio=settings.keep_audio,
                      presets=getattr(generation, "presets", None))
