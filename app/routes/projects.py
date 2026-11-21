"""Projects: shared places where clips live, and the people who can reach them.

A project is invisible to anyone who is not in it - a 404, never a 403 - so a
stranger cannot learn that one exists. Once you are in, your role decides what
you may do: the owner runs it, editors make clips, viewers watch.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..auth import current_user
from ..store import projects, users
from .shapes import avatar_url

log = logging.getLogger("h3studio.projects")

router = APIRouter(prefix="/api/projects", tags=["projects"],
                   dependencies=[Depends(current_user)])

NO_PROJECT = "no such project"
BAD_NAME = "A project needs a name of 1 to 80 characters."


async def project_scope(project: str | None = Query(default=None, max_length=64),
                        user: dict = Depends(current_user)) -> dict | None:
    """The project a list endpoint is narrowed to by ?project=, or None."""
    if project is None:
        return None
    if row := await projects.get_for(user["id"], project):
        return row
    raise HTTPException(404, NO_PROJECT)


def require(scope_or_job: dict, right: Literal["make", "owner"]) -> None:
    """Refuse (403) unless the caller's role in this project grants the right."""
    role = scope_or_job.get("my_role")
    if right == "make" and role in projects.MAKERS:
        return
    if right == "owner" and role == "owner":
        return
    raise HTTPException(403, "You can watch this project's videos, but only its owner "
                             "can let you make new ones." if right == "make"
                        else "Only the project's owner can do that.")


async def _project_or_404(user: dict, pid: str) -> dict:
    if row := await projects.get_for(user["id"], pid):
        return row
    raise HTTPException(404, NO_PROJECT)


async def _owned(user: dict, pid: str) -> dict:
    row = await _project_or_404(user, pid)
    require(row, "owner")
    return row


def _name(raw: str) -> str:
    name = raw.strip()
    if not 1 <= len(name) <= 80:
        raise HTTPException(400, BAD_NAME)
    return name


def _entry(p: dict[str, Any]) -> dict[str, Any]:
    cover = p.get("cover_job_id")
    return {"id": p["id"], "name": p["name"], "my_role": p["my_role"],
            "members": p["members"], "clips": p["clips"],
            "last_clip_at": p["last_clip_at"],
            "cover_url": f"/api/poster/{cover}" if cover else None}


async def _detail(user: dict, pid: str) -> dict[str, Any]:
    p = await _project_or_404(user, pid)
    return {"id": p["id"], "name": p["name"], "my_role": p["my_role"],
            "members": [{"user_id": m["user_id"], "email": m["email"], "role": m["role"],
                         "avatar_url": avatar_url({"id": m["user_id"],
                                                   "avatar_key": m["avatar_key"]}),
                         "is_me": m["user_id"] == user["id"]}
                        for m in await projects.members(pid)]}


class NameBody(BaseModel):
    name: str


class AddMember(BaseModel):
    email: str
    role: Literal["editor", "viewer"]


class RoleBody(BaseModel):
    role: Literal["editor", "viewer"]


@router.get("")
async def list_projects(user: dict = Depends(current_user)) -> dict[str, Any]:
    return {"projects": [_entry(p) for p in await projects.list_for(user["id"])]}


@router.post("")
async def create_project(body: NameBody, user: dict = Depends(current_user)) -> dict[str, Any]:
    made = await projects.create(user["id"], _name(body.name))
    mine = next(p for p in await projects.list_for(user["id"]) if p["id"] == made["id"])
    return _entry(mine)


@router.get("/{pid}")
async def get_project(pid: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    return await _detail(user, pid)


@router.patch("/{pid}")
async def rename_project(pid: str, body: NameBody,
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    await _owned(user, pid)
    await projects.rename(pid, _name(body.name))
    return await _detail(user, pid)


@router.delete("/{pid}")
async def delete_project(pid: str, request: Request,
                         user: dict = Depends(current_user)) -> dict[str, bool]:
    await _owned(user, pid)
    if await projects.busy(pid):
        raise HTTPException(409, "Wait until nothing in this project is waiting or being "
                                 "made, or cancel those clips first.")
    store = request.app.state.storage
    failed = False
    for key in await projects.files_of(pid):
        try:
            await store.delete(key)
        except Exception:  # noqa: BLE001
            log.warning("could not delete %s while deleting a project", key, exc_info=True)
            failed = True
    if failed:
        # Keep the rows: they still say where the leftover files are.
        raise HTTPException(502, "Some files could not be deleted, so the project was kept.")
    await projects.delete(pid)
    return {"ok": True}


@router.post("/{pid}/members")
async def add_member(pid: str, body: AddMember,
                     user: dict = Depends(current_user)) -> dict[str, Any]:
    await _owned(user, pid)
    target = await users.by_email(body.email)
    if not target or not target.get("is_active"):
        raise HTTPException(404, "Nobody has an H3 account with that email.")
    if not await projects.add_member(pid, target["id"], body.role):
        raise HTTPException(409, "They're already in this project.")
    return await _detail(user, pid)


async def _role_of(pid: str, uid: str) -> str:
    for m in await projects.members(pid):
        if m["user_id"] == uid:
            return m["role"]
    raise HTTPException(404, "They're not in this project.")


@router.patch("/{pid}/members/{uid}")
async def change_role(pid: str, uid: str, body: RoleBody,
                      user: dict = Depends(current_user)) -> dict[str, Any]:
    await _owned(user, pid)
    if await _role_of(pid, uid) == "owner":
        raise HTTPException(409, "The owner's role can't be changed.")
    await projects.set_role(pid, uid, body.role)
    return await _detail(user, pid)


@router.delete("/{pid}/members/{uid}")
async def remove_member(pid: str, uid: str,
                        user: dict = Depends(current_user)) -> dict[str, bool]:
    p = await _project_or_404(user, pid)
    if uid != user["id"]:
        require(p, "owner")
    if await _role_of(pid, uid) == "owner":
        raise HTTPException(409, "The owner can't leave. Delete the project instead.")
    await projects.remove_member(pid, uid)
    return {"ok": True}
