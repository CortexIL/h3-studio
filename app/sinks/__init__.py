"""Where finished videos go.

One interface with one implementation today. The point of the seam is the move to a
team web app: the local folder becomes object storage plus a download link, and only
this package changes - the queue, the UI and the backends stay as they are.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


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


class LocalFolderSink:
    """Writes into the folder chosen in the UI.

    Filenames are prompt-derived and prefixed with the job id, so a folder of 40 clips
    is still navigable and re-runs never silently overwrite an earlier take.
    """

    name = "local_folder"

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)

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
        return str(dest)


def make_sink(cfg: Any) -> OutputSink:
    if cfg.output.sink == "local_folder":
        return LocalFolderSink(cfg.output.folder)
    raise ValueError(f"unknown output sink: {cfg.output.sink!r}")
