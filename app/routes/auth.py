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


class PasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/password")
async def change_password(body: PasswordBody, request: Request, response: Response,
                          user: dict = Depends(auth.current_user)) -> dict:
    """Change your own password without being signed out.

    A wrong current password is a 400, never a 401: the client treats 401 as
    "your session is gone" and would bounce you to the sign-in page for a typo.
    Setting the password bumps token_version, which kills every other session
    - so this one gets a fresh cookie, and the old cookie stops working.
    """
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip, user["email"]):
        raise HTTPException(429, "too many attempts; wait five minutes")
    if await users.authenticate(user["email"], body.current_password) is None:
        raise HTTPException(400, "your current password is not right")
    try:
        await users.set_password(user["id"], body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    auth.set_cookie(response, auth.issue(await users.by_id(user["id"])))
    return {"ok": True}


@router.post("/me/sign-out-everywhere")
async def sign_out_everywhere(response: Response,
                              user: dict = Depends(auth.current_user)) -> dict:
    await users.bump_token_version(user["id"])
    auth.clear_cookie(response)
    return {"ok": True}
