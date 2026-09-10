"""The user's own finished clips."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ..auth import current_user
from ..store import jobs as jobs_store

router = APIRouter(prefix="/api", tags=["archive"],
                   dependencies=[Depends(current_user)])


@router.get("/archive")
async def archive(cursor: str | None = Query(default=None),
                  limit: int = Query(default=24, ge=1, le=100),
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """A page of finished clips.

    Each entry carries a URL rather than an object key: the browser never needs
    to know where a clip is stored, and a key in the payload is a key somebody
    will eventually try to fetch directly.
    """
    clips, next_cursor = await jobs_store.archive_page(user["id"], cursor, limit)
    return {
        "clips": [{
            "id": c["id"],
            "prompt": c["prompt"],
            "seconds": c["seconds"],
            "preset": c["preset"],
            "mode": c["mode"],
            "finished_at": c["finished_at"],
            "bytes": c["output_bytes"],
            "video_url": f"/api/video/{c['id']}",
        } for c in clips],
        "next_cursor": next_cursor,
    }
