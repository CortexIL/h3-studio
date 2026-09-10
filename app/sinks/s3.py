"""Finished clips into the object store.

Implements the same OutputSink protocol the local folder sink does, which is why
the orchestrator does not change: it hands over bytes and gets back a locator.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import storage as storage_mod
from . import slugify, strip_audio_bytes


class S3Sink:
    name = "s3"

    def __init__(self, store: Any, *, keep_audio: bool = True) -> None:
        self._store = store
        self.keep_audio = keep_audio

    async def put(self, job: dict[str, Any], data: bytes, filename: str) -> str:
        if not self.keep_audio:
            data = await asyncio.to_thread(strip_audio_bytes, data)
        # The job id is already unique, so no _2 suffix loop is needed and a
        # re-run cannot overwrite an earlier take.
        key = storage_mod.video_key(
            str(job["user_id"]), str(job["id"]), slugify(job.get("prompt", "")))
        await self._store.put(key, data, "video/mp4")
        return key
