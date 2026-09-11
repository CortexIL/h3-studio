"""Settings an admin changes at runtime.

These live in the database rather than in a file because the container filesystem
is wiped on every redeploy, and 'the GPU policy reset itself when we shipped a CSS
fix' is the kind of surprise that costs money.
"""
from __future__ import annotations

from .pool import connection


async def get(k: str, default: str | None = None) -> str | None:
    async with connection() as conn:
        row = await (await conn.execute(
            "SELECT v FROM kv WHERE k=%s", (k,))).fetchone()
    return row["v"] if row else default


async def set(k: str, v: str) -> None:  # noqa: A001 - the table is called kv
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO kv (k,v) VALUES (%s,%s)"
            " ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (k, v))
        await conn.commit()
