"""Pod sessions, so the cost of every boot is on record."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .pool import connection

COLUMNS = """
    id, pod_id, status, endpoint, gpu_type,
    EXTRACT(EPOCH FROM started_at) AS started_at,
    EXTRACT(EPOCH FROM ended_at)   AS ended_at,
    cost_estimate, note
"""


async def start(pod_id: str | None, gpu_type: str, note: str = "") -> str:
    run_id = uuid.uuid4().hex[:12]
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO runs (id, pod_id, status, gpu_type, note)"
            " VALUES (%s,%s,'booting',%s,%s)", (run_id, pod_id, gpu_type, note))
        await conn.commit()
    return run_id


async def update(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    enc = dict(fields)
    if isinstance(enc.get("ended_at"), (int, float)):
        enc["ended_at"] = datetime.fromtimestamp(enc["ended_at"], tz=timezone.utc)
    sets = ", ".join(f"{k}=%s" for k in enc)
    async with connection() as conn:
        await conn.execute(f"UPDATE runs SET {sets} WHERE id=%s",
                           (*enc.values(), run_id))
        await conn.commit()


async def recent(limit: int = 20) -> list[dict[str, Any]]:
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT {COLUMNS} FROM runs ORDER BY started_at DESC LIMIT %s", (limit,)
        )).fetchall()
    for r in rows:
        r["cost_estimate"] = float(r["cost_estimate"])
        for k in ("started_at", "ended_at"):
            if r.get(k) is not None:
                r[k] = float(r[k])
    return rows
