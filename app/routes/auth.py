"""Sign in, sign out, and who am I."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth
from ..store import users

router = APIRouter(prefix="/api", tags=["auth"])

# In-process and therefore per-replica. That is sufficient here because the
# replica count is pinned at 1 for the orchestrator's sake anyway.
_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
WINDOW_SECONDS = 300
MAX_ATTEMPTS = 10


def _rate_limited(ip: str) -> bool:
    now = time.time()
    hits = _ATTEMPTS[ip]
    while hits and hits[0] < now - WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= MAX_ATTEMPTS:
        return True
    hits.append(now)
    return False


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(429, "too many sign-in attempts; wait five minutes")
    user = await users.authenticate(body.email, body.password)
    if user is None:
        # One message for every failure. Saying which half was wrong tells an
        # attacker which addresses have accounts.
        raise HTTPException(401, "wrong email or password")
    auth.set_cookie(response, auth.issue(user))
    return {"id": user["id"], "email": user["email"], "role": user["role"]}


@router.post("/auth/logout")
async def logout(response: Response) -> dict:
    auth.clear_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(auth.current_user)) -> dict:
    return {"id": user["id"], "email": user["email"], "role": user["role"]}
