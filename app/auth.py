"""Sessions: a signed cookie, and the dependencies that guard every route.

Stateless rather than a session table, because the only thing a table would buy
here is revocation - and `users.token_version` buys that for one integer.
"""
from __future__ import annotations

import time  # noqa: F401  (tests monkeypatch it; itsdangerous reads the clock here)
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .settings import get_settings
from .store import users

COOKIE_NAME = "h3_session"
_SALT = "h3-studio-session-v1"


@lru_cache(maxsize=1)
def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=_SALT)


def issue(user: dict[str, Any]) -> str:
    return _serializer().dumps({"uid": str(user["id"]), "tv": user["token_version"]})


def read(token: str) -> tuple[str, int] | None:
    if not token:
        return None
    max_age = get_settings().session_max_age_days * 86400
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    uid, tv = data.get("uid"), data.get("tv")
    if not isinstance(uid, str) or not isinstance(tv, int):
        return None
    return uid, tv


def set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=s.session_max_age_days * 86400,
        httponly=True,            # unreadable from JavaScript, so XSS cannot steal it
        secure=s.cookie_secure,
        samesite="lax",           # blocks cross-site POSTs; the app is same-origin
        path="/",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


async def current_user(request: Request) -> dict[str, Any]:
    """Resolve the session, or 401.

    The token_version comparison is why a disabled account stops working at once
    rather than whenever its cookie happens to expire.
    """
    parsed = read(request.cookies.get(COOKIE_NAME, ""))
    if parsed is None:
        raise HTTPException(401, "not signed in")
    uid, tv = parsed
    user = await users.by_id(uid)
    if user is None or not user["is_active"] or user["token_version"] != tv:
        raise HTTPException(401, "not signed in")
    return user


async def require_admin(
        user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(403, "admins only")
    return user
