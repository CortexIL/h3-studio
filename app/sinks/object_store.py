"""Finished clips into the object store.

Named for the seam rather than for S3: the same class writes to a bucket in
production and to a directory in development, because both sit behind the same
Storage interface. The orchestrator is untouched either way - it hands over bytes
and gets back a locator.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import storage as storage_mod
from . import extract_poster, slugify, strip_audio_bytes


class ObjectSink:
    name = "object_store"

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

    async def poster(self, job: dict[str, Any], data: bytes) -> str | None:
        """Store one still frame for the clip; the key, or None if there is none."""
        image = await asyncio.to_thread(extract_poster, data)
        if not image:
            return None
        key = storage_mod.poster_key(str(job["user_id"]), str(job["id"]))
        await self._store.put(key, image, "image/jpeg")
        return key
