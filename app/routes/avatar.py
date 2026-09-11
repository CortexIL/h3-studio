"""Profile pictures.

Yours to set and remove. Visible to you and to administrators, and a 404 to
anyone else - the same rule as a clip.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from .. import avatars
from .. import storage as storage_mod
from ..auth import current_user
from ..store import users
from .shapes import me_payload

log = logging.getLogger("h3studio.avatar")

router = APIRouter(prefix="/api", tags=["avatar"], dependencies=[Depends(current_user)])

# The browser sends a cropped 512px PNG, far under this. The limit is for
# anything else that talks to the API directly.
MAX_AVATAR_BYTES = 10 * 1024 * 1024
NOT_FOUND = "no such picture"


async def _delete_quietly(store: Any, key: str) -> None:
    # The row already points at the new picture; a leftover file costs a few KB
    # and must not fail the request that replaced it.
    try:
        await store.delete(key)
    except Exception:  # noqa: BLE001
        log.warning("could not delete a replaced profile picture", exc_info=True)


@router.post("/me/avatar")
async def set_avatar(request: Request, file: UploadFile = File(...),
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    data = await file.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(413, "profile pictures are limited to 10 MB")
    try:
        webp = await run_in_threadpool(avatars.process, data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    store = request.app.state.storage
    key = storage_mod.avatar_key(user["id"])
    await store.put(key, webp, "image/webp")
    if previous := await users.set_avatar(user["id"], key):
        await _delete_quietly(store, previous)
    return me_payload(await users.by_id(user["id"]))


@router.delete("/me/avatar")
async def remove_avatar(request: Request, user: dict = Depends(current_user)) -> dict[str, Any]:
    if previous := await users.set_avatar(user["id"], None):
        await _delete_quietly(request.app.state.storage, previous)
    return me_payload(await users.by_id(user["id"]))


@router.get("/avatar/{user_id}")
async def get_avatar(user_id: str, request: Request,
                     user: dict = Depends(current_user)) -> Response:
    try:
        uid = str(uuid.UUID(user_id))
    except ValueError:
        raise HTTPException(404, NOT_FOUND) from None
    # Yours, or anyone's if you are an admin. Anyone else learns nothing - not
    # even whether the account exists.
    if uid != user["id"] and user["role"] != "admin":
        raise HTTPException(404, NOT_FOUND)
    target = await users.by_id(uid)
    if not target or not target.get("avatar_key"):
        raise HTTPException(404, NOT_FOUND)
    try:
        data = await request.app.state.storage.get(target["avatar_key"])
    except storage_mod.ObjectMissing:
        raise HTTPException(404, NOT_FOUND) from None
    # The URL carries a version that changes with the picture, so a browser
    # may keep this one for good - privately, since it is behind a sign-in.
    return Response(data, media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=31536000, immutable"})
