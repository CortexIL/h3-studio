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
#
# Two buckets, because one is always wrong. Per-IP alone locks out a whole office
# behind one NAT the moment somebody fat-fingers a password ten times; per-account
# alone lets one machine spray a thousand addresses. So: a tight limit on attempts
# against a single account from a single address, and a loose backstop on the
# address overall.
_PER_ACCOUNT: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_PER_IP: dict[str, deque[float]] = defaultdict(deque)
WINDOW_SECONDS = 300
MAX_PER_ACCOUNT = 10
MAX_PER_IP = 50


def _over(bucket: deque[float], limit: int, now: float) -> bool:
    while bucket and bucket[0] < now - WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def _rate_limited(ip: str, email: str) -> bool:
    now = time.time()
    account = _over(_PER_ACCOUNT[(ip, email.strip().lower())], MAX_PER_ACCOUNT, now)
    address = _over(_PER_IP[ip], MAX_PER_IP, now)
    return account or address


def reset_rate_limits() -> None:
    """Only for tests: the buckets are process-global by design."""
    _PER_ACCOUNT.clear()
    _PER_IP.clear()


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip, body.email):
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
