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


# How much of the old clip carries into the new one when extending.
#
# MiniMaxH3AddGuide anchors a guide clip on the model's own grid - it snaps a
# length down until `frames % 17 == 5`, giving 5, 22, 39 and so on. 22 frames is
# about nine tenths of a second at 24fps: enough movement for the new clip to
# continue the old one's motion rather than merely start on the same picture,
# and short enough that the overlap costs about a cent of render.
OVERLAP_FRAMES = 22
GUIDE_FPS = 24


def _ffprobe(path: Path, args: list[str]) -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return ""
    try:
        r = subprocess.run([ffprobe, "-v", "error", *args, str(path)],
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.decode("utf-8", "replace").strip()


def tail_clip_bytes(data: bytes, frames: int = OVERLAP_FRAMES,
                    fps: int = GUIDE_FPS) -> bytes | None:
    """The last `frames` frames of a clip, with their sound, at `fps`.

    This is what lets an extended clip continue a movement instead of merely
    starting on the same still: these frames and this audio are anchored at the
    front of the new render, so motion and sound carry across the join.

    Three things here are deliberate.

    It ends on the source's final frame, exactly. AddGuide takes the *first* N
    frames of whatever it is handed, after snapping N down to the model's grid,
    so a loose tail would anchor the wrong window and leave a gap at the seam.

    It is resampled to 24fps, the only rate the model works at. The last 22
    frames of a 30fps phone clip span three quarters of a second, and the
    continuation would carry on at the wrong speed.

    And unlike every other helper here, a failure returns None rather than the
    input. The others protect a clip that has already been rendered and paid
    for; this one decides whether a render that has not happened yet will be
    right, and a wrong tail is an invisibly broken join.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot cut a tail clip: ffmpeg is not on PATH")
        return None
    span = frames / float(fps)
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "tail.mp4"
        src.write_bytes(data)

        raw = _ffprobe(src, ["-show_entries", "format=duration", "-of", "csv=p=0"])
        try:
            duration = float(raw.split(",")[0])
        except (TypeError, ValueError):
            log.warning("cannot cut a tail clip: the source has no readable duration")
            return None
        if duration <= 0:
            return None
        start = max(0.0, duration - span)

        has_audio = bool(_ffprobe(src, ["-select_streams", "a:0", "-show_entries",
                                        "stream=index", "-of", "csv=p=0"]))
        cmd = [ffmpeg, "-v", "error", "-y", "-i", str(src),
               "-ss", f"{start:.3f}", "-vf", f"fps={fps}", "-frames:v", str(frames),
               "-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        cmd += (["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"] if has_audio else ["-an"])
        cmd.append(str(dst))
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=300,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("tail cut failed: %s", e)
            return None
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
            log.warning("tail cut produced nothing usable: %s",
                        r.stderr.decode("utf-8", "replace")[:200])
            return None
        if has_audio:
            return dst.read_bytes()

        # A silent source gets silence laid over the cut, rather than during it:
        # generated silence cannot be seeked into the way a real track can, and
        # trying turns the whole cut into an empty file. H3 reads the guide's
        # audio, and an absent track is a crash on the pod, not a quiet omission.
        return _with_silence(ffmpeg, dst, Path(td) / "tail-silent.mp4")


def _with_silence(ffmpeg: str, src: Path, dst: Path) -> bytes | None:
    """The same clip with a silent audio track. The picture is copied, not re-encoded."""
    try:
        r = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(src),
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "128k", "-shortest",
             "-movflags", "+faststart", str(dst)],
            capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("could not add a silent track to the tail clip: %s", e)
        return None
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
        log.warning("could not add a silent track to the tail clip")
        return None
    return dst.read_bytes()


def drop_leading_frames(data: bytes, frames: int = OVERLAP_FRAMES,
                        fps: int = GUIDE_FPS) -> bytes:
    """Remove the first `frames` frames and their audio, returning new bytes.

    An extended clip opens by reproducing the tail it was asked to continue - that
    reproduction is what makes the movement carry across the join. Once it has
    served that purpose it is duplicate footage, so it comes off here and the two
    clips lie end to end with nothing to trim by hand. That is the whole promise
    of the mode.

    Any failure returns the input untouched: an extra second at the front of a
    clip that has already been rendered and paid for is a far better outcome than
    no clip at all.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot trim the overlap: ffmpeg is not on PATH")
        return data
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.mp4", Path(td) / "out.mp4"
        src.write_bytes(data)
        try:
            r = subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", str(src),
                 "-ss", f"{frames / float(fps):.4f}",
                 "-map", "0:v", "-map", "0:a?",
                 "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)],
                capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("trimming the overlap failed: %s", e)
            return data
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
            log.warning("trimming the overlap produced nothing usable; keeping the render")
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
