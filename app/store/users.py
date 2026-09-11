"""User rows and password handling.

Passwords are argon2 at library defaults. `token_version` is what makes stateless
session cookies revocable: bumping it invalidates every cookie already issued to
that user without keeping a session table.
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerifyMismatchError

from .pool import connection

log = logging.getLogger("h3studio.users")

_ph = PasswordHasher()

# Verified against when the email does not exist, so a missing account costs the
# same work as a wrong password and response time does not enumerate users.
_DUMMY_HASH = _ph.hash("a-password-that-is-never-anyone-s")

MIN_PASSWORD = 8

PUBLIC_COLUMNS = ("id::text AS id, email, role, is_active, token_version, created_at,"
                  " avatar_key")


class EmailTaken(ValueError):
    pass


def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(hash_: str, raw: str) -> bool:
    try:
        return _ph.verify(hash_, raw)
    except (VerifyMismatchError, Argon2Error, TypeError, ValueError):
        return False


def _norm(email: str) -> str:
    return email.strip().lower()


async def create(email: str, password: str, *, role: str = "user") -> dict[str, Any]:
    if role not in {"user", "admin"}:
        raise ValueError(f"unknown role {role!r}")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    if "@" not in _norm(email):
        raise ValueError("that does not look like an email address")
    async with connection() as conn:
        try:
            row = await (await conn.execute(
                f"INSERT INTO users (email, password_hash, role) VALUES (%s,%s,%s)"
                f" RETURNING {PUBLIC_COLUMNS}",
                (_norm(email), hash_password(password), role),
            )).fetchone()
        except psycopg.errors.UniqueViolation:
            raise EmailTaken(f"{_norm(email)} already has an account") from None
        await conn.commit()
    return row


async def by_id(user_id: str) -> dict[str, Any] | None:
    async with connection() as conn:
        return await (await conn.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM users WHERE id=%s", (user_id,)
        )).fetchone()


async def by_email(email: str) -> dict[str, Any] | None:
    async with connection() as conn:
        return await (await conn.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM users WHERE email=%s", (_norm(email),)
        )).fetchone()


async def authenticate(email: str, password: str) -> dict[str, Any] | None:
    async with connection() as conn:
        row = await (await conn.execute(
            f"SELECT {PUBLIC_COLUMNS}, password_hash FROM users WHERE email=%s",
            (_norm(email),),
        )).fetchone()
    if row is None:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(row["password_hash"], password):
        return None
    if not row["is_active"]:
        return None
    row.pop("password_hash")
    return row


async def set_active(user_id: str, active: bool) -> None:
    # Bumping the version on disable is the whole revocation mechanism: without
    # it a disabled user keeps working until their cookie happens to expire.
    async with connection() as conn:
        await conn.execute(
            "UPDATE users SET is_active=%s, token_version=token_version+1"
            " WHERE id=%s", (active, user_id))
        await conn.commit()


async def set_password(user_id: str, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    async with connection() as conn:
        await conn.execute(
            "UPDATE users SET password_hash=%s, token_version=token_version+1"
            " WHERE id=%s", (hash_password(password), user_id))
        await conn.commit()


async def bump_token_version(user_id: str) -> None:
    """Invalidate every session cookie this user holds, on every device."""
    async with connection() as conn:
        await conn.execute(
            "UPDATE users SET token_version=token_version+1 WHERE id=%s", (user_id,))
        await conn.commit()


async def set_role(user_id: str, role: str) -> None:
    if role not in {"user", "admin"}:
        raise ValueError(f"unknown role {role!r}")
    async with connection() as conn:
        await conn.execute("UPDATE users SET role=%s WHERE id=%s", (role, user_id))
        await conn.commit()


async def set_avatar(user_id: str, key: str | None) -> str | None:
    """Point the user at a new picture, or at none. Returns the key it replaced,
    so the caller can delete that file."""
    async with connection() as conn:
        row = await (await conn.execute(
            "UPDATE users u SET avatar_key=%s"
            " FROM (SELECT id, avatar_key FROM users WHERE id=%s FOR UPDATE) old"
            " WHERE u.id = old.id RETURNING old.avatar_key AS previous",
            (key, user_id))).fetchone()
        await conn.commit()
    return row["previous"] if row else None


async def list_all() -> list[dict[str, Any]]:
    async with connection() as conn:
        return await (await conn.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM users ORDER BY created_at"
        )).fetchall()


async def count() -> int:
    async with connection() as conn:
        row = await (await conn.execute("SELECT COUNT(*) AS n FROM users")).fetchone()
    return int(row["n"])


async def ensure_bootstrap_admin(email: str | None,
                                 password: str | None) -> dict[str, Any] | None:
    """Create the first admin from the environment, once.

    Deploying to a fresh database otherwise leaves nobody able to log in and no
    shell to fix it from. Guarded on the table being empty rather than on the
    email being absent, so rotating ADMIN_PASSWORD later cannot silently mint a
    second admin.
    """
    if not email or not password:
        return None
    if await count() > 0:
        return None
    admin = await create(email, password, role="admin")
    log.info("created the first admin account (%s)", admin["email"])
    return admin
