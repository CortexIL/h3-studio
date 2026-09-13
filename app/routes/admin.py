"""Everything one person controls on everyone's behalf.

Separate routes rather than a role branch inside the normal ones: there is no
path here that a non-admin can reach at all, so there is no branch to get wrong.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth, prompting
from ..auth import require_admin
from ..store import jobs as jobs_store
from ..store import kv, runs, users
from .shapes import public_user

router = APIRouter(prefix="/api/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])

MAX_BUDGET_USD = 1000.0


class NewUser(BaseModel):
    email: str
    password: str
    role: str = "user"


class UserPatch(BaseModel):
    is_active: bool | None = None
    password: str | None = None
    role: str | None = None


class PolicyBody(BaseModel):
    policy: str


class KeyBody(BaseModel):
    key: str


class BudgetBody(BaseModel):
    session_limit_usd: float


@router.get("/users")
async def list_users() -> dict[str, Any]:
    usage = await jobs_store.usage_by_user()
    rows = await users.list_all()
    return {"users": [public_user(u) | {"usage": usage.get(u["id"], jobs_store.empty_usage())}
                      for u in rows]}


@router.post("/users")
async def create_user(body: NewUser) -> dict[str, Any]:
    try:
        return public_user(await users.create(body.email, body.password, role=body.role))
    except users.EmailTaken as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, body: UserPatch, response: Response,
                     admin: dict = Depends(require_admin)) -> dict[str, Any]:
    target = await users.by_id(user_id)
    if target is None:
        raise HTTPException(404, "no such user")
    # Locking the last admin out of their own instance needs a database console
    # to undo, so refuse the two obvious ways of doing it to yourself.
    is_self = str(target["id"]) == str(admin["id"])
    if is_self and body.is_active is False:
        raise HTTPException(400, "you cannot disable your own account")
    if is_self and body.role is not None and body.role != "admin":
        raise HTTPException(400, "you cannot remove your own admin role")

    if body.is_active is not None:
        await users.set_active(user_id, body.is_active)
    try:
        if body.password is not None:
            await users.set_password(user_id, body.password)
            if is_self:
                # The reset bumped token_version; without a fresh cookie the
                # admin would sign themselves out by resetting their own password.
                response_cookie = auth.issue(await users.by_id(user_id))
                auth.set_cookie(response, response_cookie)
        if body.role is not None:
            await users.set_role(user_id, body.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return public_user(await users.by_id(user_id))


@router.get("/jobs")
async def all_jobs() -> dict[str, Any]:
    return {"jobs": await jobs_store.list_all()}


@router.get("/status")
async def admin_status(request: Request) -> dict[str, Any]:
    return await request.app.state.orch.snapshot()


@router.post("/policy")
async def set_policy(body: PolicyBody, request: Request) -> dict[str, Any]:
    orch = request.app.state.orch
    try:
        await orch.set_policy(body.policy)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"policy": await orch.policy()}


@router.post("/budget")
async def set_budget(body: BudgetBody, request: Request) -> dict[str, Any]:
    if not 0 < body.session_limit_usd <= MAX_BUDGET_USD:
        raise HTTPException(
            400, f"the session limit must be between 0 and {MAX_BUDGET_USD:.0f} USD")
    await kv.set("budget_session_limit_usd", str(body.session_limit_usd))
    request.app.state.cfg.budget.session_limit_usd = body.session_limit_usd
    return {"session_limit_usd": body.session_limit_usd}


class SageBody(BaseModel):
    enabled: bool


@router.post("/sage")
async def set_sage(body: SageBody) -> dict[str, Any]:
    """The experimental faster attention, on or off for every clip from now on."""
    await kv.set("sage_attention", "on" if body.enabled else "off")
    return {"sage": body.enabled}


@router.post("/runpod-key")
async def set_runpod_key(body: KeyBody, request: Request) -> dict[str, Any]:
    """Save the RunPod key, after RunPod agrees it is real.

    A typo saved silently would only surface later as a failed pod start, by
    which point the operator has no idea which of several things went wrong.

    Stored in the database, not a file: a container filesystem does not survive a
    redeploy. Never returned - only its last four characters, which is enough to
    recognise which key is loaded.
    """
    key = body.key.strip()
    if not key:
        raise HTTPException(400, "no key given")
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get("https://rest.runpod.io/v1/pods",
                            headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"could not reach RunPod to verify the key: {e}")
    if r.status_code == 401:
        raise HTTPException(400, "RunPod rejected that key. Check you copied all "
                                 "of it, and that its permission level is All.")
    if r.status_code >= 400:
        raise HTTPException(400, f"RunPod returned {r.status_code} for that key")

    await kv.set("runpod_api_key", key)
    request.app.state.cfg.runpod.api_key = key
    return {"ok": True, "hint": key[-4:], "restart_required": True,
            "note": "Key verified and saved. Redeploy or restart to use it - the "
                    "backend is chosen when the process starts."}


@router.get("/key-state")
async def key_state(request: Request) -> dict[str, Any]:
    """Whether a key is loaded, and which one - by its last four characters only."""
    key = request.app.state.cfg.runpod.api_key
    return {"present": bool(key), "hint": key[-4:] if key else ""}


@router.post("/prompt-key")
async def set_prompt_key(body: KeyBody, request: Request) -> dict[str, Any]:
    """Save the prompt helper's key, after the API agrees it is real.

    Same handling as the RunPod key: verified first, kept in the database, never
    returned beyond its last four characters. Takes effect at once - the helper
    reads the key per request, so no restart.
    """
    key = body.key.strip()
    if not key:
        raise HTTPException(400, "no key given")
    try:
        await prompting.verify_key(key)
    except prompting.PromptHelperError as e:
        raise HTTPException(400, str(e))
    await kv.set("anthropic_api_key", key)
    request.app.state.cfg.anthropic_api_key = key
    return {"ok": True, "hint": key[-4:]}


@router.get("/prompt-key-state")
async def prompt_key_state(request: Request) -> dict[str, Any]:
    key = request.app.state.cfg.anthropic_api_key
    return {"present": bool(key), "hint": key[-4:] if key else ""}


@router.get("/runs")
async def recent_runs() -> dict[str, Any]:
    rows = await runs.recent()
    return {"runs": rows,
            "total_cost_usd": round(sum(r["cost_estimate"] for r in rows), 4)}
