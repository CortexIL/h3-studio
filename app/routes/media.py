"""Bytes in and bytes out: reference uploads, video playback.

Everything here is decided by the database row or by the caller's own key prefix,
never by the shape of a key the browser claims to own.
"""
from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from urllib.parse import quote
from typing import Any

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Request,
                     Response, UploadFile)
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import batch
from .. import storage as storage_mod
from ..sinks import audio_track_bytes, reference_video_bytes, slugify, tail_clip_bytes
from ..auth import current_user
from ..store import jobs as jobs_store

router = APIRouter(prefix="/api", tags=["media"],
                   dependencies=[Depends(current_user)])

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_BATCH_BYTES = 256 * 1024 * 1024

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
# Bigger than a reference image, because a phone clip is, and far smaller than a
# batch: only the last second is ever kept, so there is no reason to hold a whole
# film in memory to throw it away.
MAX_VIDEO_BYTES = 128 * 1024 * 1024

AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".aiff", ".aif"}
MAX_AUDIO_BYTES = 30 * 1024 * 1024


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


@router.post("/upload/video")
async def upload_video(request: Request, file: UploadFile = File(...),
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """A video from the user's own machine, for a clip that continues it.

    Its own route rather than a wider /api/upload: that one's suffix check is the
    only thing stopping a video becoming a reference image, where it would reach
    LoadImage and fail on a rented GPU - a paid failure for a mistake catchable
    here.

    Only the tail is ever used, so only the tail is kept. What lands in the bucket
    is a fresh encode at the rate the model works at, which means nothing from the
    original file - container quirks, metadata, whatever a phone wrote into it -
    survives, the same argument the avatar route makes for pictures.
    """
    suffix = PurePosixPath(file.filename or "clip.mp4").suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(400, f"{suffix or 'that file'} is not a video")
    data = await file.read(MAX_VIDEO_BYTES + 1)
    if len(data) > MAX_VIDEO_BYTES:
        raise HTTPException(413, "videos are limited to 128 MB")

    tail = await run_in_threadpool(tail_clip_bytes, data)
    if tail is None:
        raise HTTPException(400, "that video could not be read")
    key = storage_mod.upload_key(user["id"], ".mp4")
    await request.app.state.storage.put(key, tail, "video/mp4")
    return {"key": key, "name": PurePosixPath(file.filename or key).name}


@router.post("/upload/refvideo")
async def upload_reference_video(request: Request, file: UploadFile = File(...),
                                 user: dict = Depends(current_user)) -> dict[str, Any]:
    """A whole short video as a reference: identity, style, motion, a camera move.

    Unlike /upload/video, which keeps only the tail a continuation needs, this
    keeps the clip - re-encoded at the size and rate the model reads references
    at, so nothing larger than that ever reaches the bucket or the pod.
    """
    suffix = PurePosixPath(file.filename or "ref.mp4").suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(400, f"{suffix or 'that file'} is not a video")
    data = await file.read(MAX_VIDEO_BYTES + 1)
    if len(data) > MAX_VIDEO_BYTES:
        raise HTTPException(413, "videos are limited to 128 MB")
    clip = await run_in_threadpool(reference_video_bytes, data)
    if clip is None:
        raise HTTPException(400, "that video could not be read")
    key = storage_mod.upload_key(user["id"], ".mp4")
    await request.app.state.storage.put(key, clip, "video/mp4")
    return {"key": key, "name": PurePosixPath(file.filename or key).name}


@router.post("/upload/audio")
async def upload_audio(request: Request, file: UploadFile = File(...),
                       user: dict = Depends(current_user)) -> dict[str, Any]:
    """A voice or audio track for the clip to follow.

    Re-encoded to one format at the clip's own length, for the same reasons the
    video route re-encodes: the pod meets a known file, and nothing from the
    original survives into the bucket.
    """
    suffix = PurePosixPath(file.filename or "track.wav").suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        raise HTTPException(400, f"{suffix or 'that file'} is not an audio file")
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "audio files are limited to 30 MB")
    track = await run_in_threadpool(audio_track_bytes, data)
    if track is None:
        raise HTTPException(400, "that audio could not be read")
    key = storage_mod.upload_key(user["id"], ".wav")
    await request.app.state.storage.put(key, track, "audio/wav")
    return {"key": key, "name": PurePosixPath(file.filename or key).name}


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
                download: bool = Query(default=False),
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
    if download:
        # An ASCII fallback for old clients, and the prompt-based name (Hebrew
        # included) for everything that reads filename*.
        pretty = quote(f"{slugify(job['prompt'])}-{job_id}.mp4")
        headers["Content-Disposition"] = (
            f'attachment; filename="h3-{job_id}.mp4"; filename*=UTF-8\'\'{pretty}')
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


@router.get("/poster/{job_id}")
async def poster(job_id: str, request: Request,
                 user: dict = Depends(current_user)):
    """One still frame of a finished clip, scoped exactly like the video."""
    job = await jobs_store.get_for(user["id"], job_id)
    if job is None or not job.get("poster_key"):
        raise HTTPException(404, "no poster for that clip")
    try:
        data = await request.app.state.storage.get(job["poster_key"])
    except storage_mod.ObjectMissing:
        raise HTTPException(404, "no poster for that clip")
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})
