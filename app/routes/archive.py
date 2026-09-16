"""The user's own finished clips: browse, search, download, and delete for good."""
from __future__ import annotations

import logging
import zipfile
from datetime import date
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import current_user
from ..sinks import slugify
from ..store import jobs as jobs_store
from .shapes import public_clip

log = logging.getLogger("h3studio.archive")

router = APIRouter(prefix="/api", tags=["archive"],
                   dependencies=[Depends(current_user)])

# A ceiling rather than a promise: the whole archive in one request is a fine
# thing to want, but an unbounded id list is a request that never ends.
MAX_BULK = 200


@router.get("/archive")
async def archive(cursor: str | None = Query(default=None),
                  limit: int = Query(default=24, ge=1, le=100),
                  q: str | None = Query(default=None, max_length=200),
                  preset: str | None = Query(default=None, max_length=40),
                  mode: str | None = Query(default=None, max_length=8),
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """A page of finished clips, optionally searched by prompt and filtered.

    Each entry carries URLs rather than object keys: the browser never needs to
    know where a clip is stored, and a key in the payload is a key somebody will
    eventually try to fetch directly.
    """
    clips, next_cursor = await jobs_store.archive_page(
        user["id"], cursor, limit, q=q, preset=preset, mode=mode)
    return {"clips": [public_clip(c) for c in clips], "next_cursor": next_cursor}


class _ZipSink:
    """A file-like object that hands each block it is given straight to the client.

    zipfile writes strictly forwards, and against a stream it cannot seek it
    emits data descriptors rather than going back to patch headers. So a zip can
    be produced without the whole archive ever existing anywhere at once - which
    matters when a batch is a hundred and forty megabytes and the process serving
    it also owns a rented GPU.
    """

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._parts.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        out = b"".join(self._parts)
        self._parts.clear()
        return out


async def _zip_stream(store: Any, entries: list[tuple[str, str]]) -> AsyncIterator[bytes]:
    """Yield a zip of `entries`, one (filename, object key) at a time.

    Stored, not deflated: an mp4 is already compressed, so the only thing
    squeezing it again would cost is time.
    """
    sink = _ZipSink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as zf:
        for name, key in entries:
            zf.writestr(name, await store.get(key))
            block = sink.drain()
            if block:
                yield block
    tail = sink.drain()          # the central directory, written on close
    if tail:
        yield tail


@router.get("/archive/download")
async def download_many(request: Request, ids: str = Query(..., max_length=4000),
                        user: dict = Depends(current_user)) -> StreamingResponse:
    """Several clips as one zip.

    Every id is resolved through the caller's own scoped read, so a list naming
    someone else's clip yields that clip's absence rather than its contents - and
    an id that is simply gone is skipped instead of failing the whole download,
    because losing a batch of thirty over one deleted clip is the worse outcome.
    """
    wanted = [i.strip() for i in ids.split(",") if i.strip()][:MAX_BULK]
    if not wanted:
        raise HTTPException(400, "no clips given")

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for job_id in wanted:
        if job_id in seen:
            continue
        seen.add(job_id)
        job = await jobs_store.get_for(user["id"], job_id)
        if job is None or job["status"] != "done" or not job.get("output_key"):
            continue
        entries.append((f"{slugify(job['prompt'])}-{job_id}.mp4", job["output_key"]))
    if not entries:
        raise HTTPException(404, "none of those clips are available")

    name = f"h3-clips-{date.today().isoformat()}-{len(entries)}.zip"
    return StreamingResponse(
        _zip_stream(request.app.state.storage, entries),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


async def _delete_one(store: Any, user_id: str, job_id: str) -> bool:
    """Delete one finished clip and its files. False if it was kept.

    The files go first and the row last: if the store refuses, the row stays
    and the user can try again, rather than a row-less file sitting in the
    bucket forever with nothing pointing at it.
    """
    job = await jobs_store.get_for(user_id, job_id)
    if job is None or job["status"] != "done" or not job.get("output_key"):
        return False
    await store.delete(job["output_key"])
    if job.get("poster_key"):
        await store.delete(job["poster_key"])
    return await jobs_store.delete_for(user_id, job_id)


@router.delete("/archive/{job_id}")
async def delete_clip(job_id: str, request: Request,
                      user: dict = Depends(current_user)) -> dict[str, Any]:
    """Delete a finished clip and its files, for good."""
    try:
        gone = await _delete_one(request.app.state.storage, user["id"], job_id)
    except Exception:
        raise HTTPException(502, "the file could not be deleted, so the clip was kept")
    if not gone:
        raise HTTPException(404, "no such clip")
    return {"ok": True}


class DeleteMany(BaseModel):
    """Clip ids to delete, as the archive's selection sends them."""
    ids: list[str] = Field(default_factory=list)


@router.post("/archive/delete")
async def delete_many(body: DeleteMany, request: Request,
                      user: dict = Depends(current_user)) -> dict[str, Any]:
    """Delete a selection of clips and their files, for good.

    A selection is deleted clip by clip and one that will not go is skipped
    rather than failing the rest: half of a selection is a partial success the
    user can see and repeat, while an abort at the tenth of two hundred leaves
    them with no idea which nine went. The reply counts both, and a selection
    where nothing at all could be deleted is an error - that one is not a
    partial success, it is a failure with a tidy face on it.

    POST rather than DELETE because a body on a DELETE is poorly supported by
    proxies, and this is how the rest of the app spells a bulk action.
    """
    wanted = list(dict.fromkeys(i.strip() for i in body.ids if i.strip()))[:MAX_BULK]
    if not wanted:
        raise HTTPException(400, "no clips given")
    store = request.app.state.storage
    deleted, kept = 0, 0
    for job_id in wanted:
        try:
            if await _delete_one(store, user["id"], job_id):
                deleted += 1
            # Anything else is already gone, or was never theirs to begin with:
            # not deleted by this call, but not kept either.
        except Exception:
            log.warning("clip %s could not be deleted", job_id, exc_info=True)
            kept += 1
    if deleted == 0 and kept:
        raise HTTPException(502, "none of those clips could be deleted")
    return {"deleted": deleted, "kept": kept}
