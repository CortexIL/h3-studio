"""The user's own finished clips: browse, search, and delete for good."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import current_user
from ..store import jobs as jobs_store
from .shapes import public_clip

router = APIRouter(prefix="/api", tags=["archive"],
                   dependencies=[Depends(current_user)])


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
