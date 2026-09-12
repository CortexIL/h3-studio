"""The user's own finished clips: browse, search, download, and delete for good."""
from __future__ import annotations

import zipfile
from datetime import date
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import current_user
from ..sinks import slugify
from ..store import jobs as jobs_store
from .shapes import public_clip

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


@router.delete("/archive/{job_id}")
async def delete_clip(job_id: str, request: Request,
                      user: dict = Depends(current_user)) -> dict[str, Any]:
    """Delete a finished clip and its files, for good.

    The files go first and the row last: if the store refuses, the row stays
    and the user can try again, rather than a row-less file sitting in the
    bucket forever with nothing pointing at it.
    """
    job = await jobs_store.get_for(user["id"], job_id)
    if job is None or job["status"] != "done" or not job.get("output_key"):
        raise HTTPException(404, "no such clip")
    store = request.app.state.storage
    try:
        await store.delete(job["output_key"])
        if job.get("poster_key"):
            await store.delete(job["poster_key"])
    except Exception:
        raise HTTPException(502, "the file could not be deleted, so the clip was kept")
    await jobs_store.delete_for(user["id"], job_id)
    return {"ok": True}
