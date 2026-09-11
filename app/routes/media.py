"""Bytes in and bytes out: reference uploads, video playback.

Everything here is decided by the database row or by the caller's own key prefix,
never by the shape of a key the browser claims to own.
"""
from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from typing import Any

from fastapi import (APIRouter, Depends, File, HTTPException, Request, Response,
                     UploadFile)
from fastapi.responses import StreamingResponse

from .. import batch
from .. import storage as storage_mod
from ..auth import current_user
from ..store import jobs as jobs_store

router = APIRouter(prefix="/api", tags=["media"],
                   dependencies=[Depends(current_user)])

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_BATCH_BYTES = 256 * 1024 * 1024


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
    # Upload keys are random and never overwritten, so the browser may keep them
    # for good - the feed re-renders reference thumbnails on every change.
    return Response(data,
                    media_type=mimetypes.guess_type(key)[0] or "application/octet-stream",
                    headers={"Cache-Control": "private, max-age=31536000, immutable"})


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


@router.post("/inbox/upload")
async def upload_batch(request: Request, file: UploadFile = File(...),
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """Drop an image, a zip or a batch file onto the page.

    Queuing is what dropping a batch in is meant to do; the pod policy still
    governs whether a GPU actually starts, and the budget ceiling remains the
    backstop either way.
    """
    name = PurePosixPath((file.filename or "dropped").replace("\\", "/")).name
    suffix = PurePosixPath(name).suffix.lower()
    accepted = batch.ARCHIVE_SUFFIXES | batch.BATCH_SUFFIXES | batch.IMAGE_SUFFIXES
    if suffix not in accepted:
        raise HTTPException(400, f"{name}: H3 Studio takes images, .zip archives, "
                                 f"or .txt/.json batch files")
    data = await file.read()
    if len(data) > MAX_BATCH_BYTES:
        raise HTTPException(413, "uploads are limited to 256 MB")
    store = request.app.state.storage
    cfg = request.app.state.cfg

    if suffix in batch.IMAGE_SUFFIXES:
        key = storage_mod.upload_key(user["id"], suffix)
        await store.put(key, data, file.content_type or "image/png")
        return {"queued": 0, "images": {name: key}, "missing_images": []}

    try:
        if suffix in batch.ARCHIVE_SUFFIXES:
            parsed, images = await batch.unpack_zip(data, user["id"], store)
        else:
            parsed = batch.parse(data.decode("utf-8-sig", "replace"), suffix)
            images = {}
    except ValueError as e:
        raise HTTPException(400, f"{name}: {e}")

    missing: list[str] = []
    queued = 0
    for job in parsed:
        refs = []
        for ref_name in job["ref_names"]:
            if key := images.get(ref_name):
                refs.append(key)
            else:
                missing.append(ref_name)
        preset = job["preset"] if job["preset"] in cfg.generation.presets \
            else cfg.generation.default_preset
        mode = job["mode"] or cfg.generation.default_mode
        for _ in range(job["takes"]):
            await jobs_store.add(user["id"], job["prompt"], seconds=job["seconds"],
                                 ref_images=refs, mode=mode, preset=preset)
            queued += 1
    return {"queued": queued, "images": images,
            "missing_images": sorted(set(missing))}
