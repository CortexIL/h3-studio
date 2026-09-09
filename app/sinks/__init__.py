"""Where finished videos go.

One interface with one implementation today. The point of the seam is the move to a
team web app: the local folder becomes object storage plus a download link, and only
this package changes - the queue, the UI and the backends stay as they are.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import time
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


def strip_audio(path: Path) -> bool:
    """Drop the audio stream, keeping the video bit-for-bit.

    H3 emits audio with every clip, and under a step-distilled LoRA that audio is
    noise: the 4-step schedule leaves the audio latent undenoised, measured at
    -13.9 dB flat across a whole batch against -34.8 dB for the same prompt at 30
    steps. `-c copy` means the video is remuxed, never re-encoded, so this costs
    milliseconds and cannot degrade the picture.

    Returns whether the file was changed. Any failure - no ffmpeg on the machine, a
    malformed file - leaves the original exactly as it was: a clip with unwanted
    audio is a far better outcome than no clip.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("cannot strip audio: ffmpeg is not on PATH; keeping %s as is",
                    path.name)
        return False

    tmp = path.with_name(path.stem + ".noaudio" + path.suffix)
    try:
        r = subprocess.run(
            [ffmpeg, "-v", "error", "-y", "-i", str(path),
             "-map", "0:v", "-c", "copy", "-an", str(tmp)],
            capture_output=True, timeout=120,
        )
        # Trust the file, not the exit code: ffmpeg can return 0 having written
        # nothing usable, and replacing a good clip with an empty one is the one
        # failure that would actually lose work.
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
            log.warning("audio strip failed for %s: %s", path.name,
                        r.stderr.decode("utf-8", "replace")[:200])
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(path)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("audio strip failed for %s: %s", path.name, e)
        tmp.unlink(missing_ok=True)
        return False


class LocalFolderSink:
    """Writes into the folder chosen in the UI.

    Filenames are prompt-derived and prefixed with the job id, so a folder of 40 clips
    is still navigable and re-runs never silently overwrite an earlier take.
    """

    name = "local_folder"

    def __init__(self, folder: str | Path, keep_audio: bool = True) -> None:
        self.folder = Path(folder)
        self.keep_audio = keep_audio

    def set_folder(self, folder: str | Path) -> None:
        self.folder = Path(folder)

    async def put(self, job: dict[str, Any], data: bytes, filename: str) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix or ".mp4"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"{stamp}_{job.get('id', 'job')}_{slugify(job.get('prompt', ''))}"
        dest = self.folder / f"{base}{ext}"
        n = 2
        while dest.exists():
            dest = self.folder / f"{base}_{n}{ext}"
            n += 1
        dest.write_bytes(data)
        if not self.keep_audio:
            await asyncio.to_thread(strip_audio, dest)
        return str(dest)


def make_sink(cfg: Any) -> OutputSink:
    if cfg.output.sink == "local_folder":
        return LocalFolderSink(cfg.output.folder, keep_audio=cfg.output.keep_audio)
    raise ValueError(f"unknown output sink: {cfg.output.sink!r}")
