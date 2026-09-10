"""Bytes in and bytes out: reference uploads, video playback.

Everything here is decided by the database row or by the caller's own key prefix,
never by the shape of a key the browser claims to own.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from fastapi import (APIRouter, Depends, File, HTTPException, Request, Response,
                     UploadFile)
from fastapi.responses import StreamingResponse

from .. import storage as storage_mod
from ..auth import current_user
from ..store import jobs as jobs_store

router = APIRouter(prefix="/api", tags=["media"],
                   dependencies=[Depends(current_user)])

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MAX_UPLOAD_BYTES = 32 * 1024 * 1024


def owns_key(user_id: str, key: str) -> bool:
    return key.startswith(f"uploads/{user_id}/") and ".." not in key


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...),
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    suffix = PurePosixPath(file.filename or "ref.png").suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise HTTPException(400, f"{suffix or 'that file'} is not an image")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "reference images are limited to 32 MB")
    key = storage_mod.upload_key(user["id"], suffix)
    await request.app.state.storage.put(key, data,
                                        file.content_type or "image/png")
    return {"key": key, "name": PurePosixPath(key).name}


@router.get("/image/{key:path}")
async def get_image(key: str, request: Request,
                    user: dict = Depends(current_user)):
    """Serve one of this user's own uploads.

    The prefix check is the authorization: an upload is not attached to a job row
    until it is used, so there is nothing else to check it against.
    """
    if not owns_key(user["id"], key):
        raise HTTPException(404, "no such image")
    try:
        data = await request.app.state.storage.get(key)
    except storage_mod.ObjectMissing:
        raise HTTPException(404, "no such image")
    return Response(data, media_type="image/png")


@router.get("/video/{job_id}")
async def video(job_id: str, request: Request,
                user: dict = Depends(current_user)):
    job = await jobs_store.get_for(user["id"], job_id)
    if job is None or not job.get("output_key"):
        raise HTTPException(404, "no output for that job")
    store = request.app.state.storage
    byte_range = request.headers.get("range")
    try:
        chunks, size, content_range = await store.stream(job["output_key"],
                                                         byte_range)
    except storage_mod.ObjectMissing:
        raise HTTPException(404, "that clip is no longer stored")
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(size)}
    status_code = 200
    if content_range:
        headers["Content-Range"] = content_range
        status_code = 206
    return StreamingResponse(chunks, status_code=status_code,
                             media_type="video/mp4", headers=headers)
