"""Projects and who is in them.

A project is the unit clips are shared by: every member can see its clips, and
the role decides what else they may do. This module only stores; what a role
may do is decided by the routes. `jobs` imports this module (to find the
default "My clips" project), so it must never import `jobs`.
"""
from __future__ import annotations

import uuid
from typing import Any

from .pool import connection

ROLES = ("owner", "editor", "viewer")
MAKERS = ("owner", "editor")
PERSONAL_NAME = "My clips"
# The first key of personal_for's two-key advisory lock (0x4833, "H3"): the
# two-key form keeps it out of the one-key space the orchestrator's lock uses.
LOCK_NAMESPACE = 18483


def _epoch(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    for k in keys:
        if row.get(k) is not None:
            row[k] = float(row[k])
    return row


async def create(owner_id: str, name: str) -> dict[str, Any]:
    async with connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO projects (name, created_by) VALUES (%s, %s)"
            " RETURNING id::text AS id, name,"
            " EXTRACT(EPOCH FROM created_at) AS created_at", (name, owner_id))).fetchone()
        await conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (%s,%s,'owner')",
            (row["id"], owner_id))
        await conn.commit()
    return _epoch(row, "created_at")


async def get_for(user_id: str, project_id: str) -> dict[str, Any] | None:
    try:
        uuid.UUID(project_id)
    except (ValueError, AttributeError, TypeError):
        return None
    async with connection() as conn:
        row = await (await conn.execute(
            "SELECT p.id::text AS id, p.name,"
            " EXTRACT(EPOCH FROM p.created_at) AS created_at, m.role AS my_role"
            " FROM projects p JOIN project_members m ON m.project_id = p.id"
            " WHERE p.id=%s AND m.user_id=%s", (project_id, user_id))).fetchone()
    return _epoch(row, "created_at") if row else None


async def list_for(user_id: str) -> list[dict[str, Any]]:
    async with connection() as conn:
        rows = await (await conn.execute(
            "SELECT p.id::text AS id, p.name, m.role AS my_role,"
            " (SELECT count(*) FROM project_members x WHERE x.project_id = p.id)::int AS members,"
            # Finished videos only, the same ones the Clips tab shows.
            " (SELECT count(*) FROM jobs j WHERE j.project_id = p.id AND j.status = 'done'"
            "   AND j.output_key IS NOT NULL)::int AS clips,"
            " (SELECT EXTRACT(EPOCH FROM max(j.created_at)) FROM jobs j"
            "   WHERE j.project_id = p.id) AS last_clip_at,"
            " (SELECT j.id FROM jobs j WHERE j.project_id = p.id AND j.status = 'done'"
            "   AND j.poster_key IS NOT NULL ORDER BY j.created_at DESC LIMIT 1) AS cover_job_id"
            " FROM projects p JOIN project_members m ON m.project_id = p.id"
            " WHERE m.user_id=%s"
            " ORDER BY GREATEST((SELECT max(j.created_at) FROM jobs j WHERE j.project_id = p.id),"
            " p.created_at) DESC", (user_id,))).fetchall()
    return [_epoch(r, "last_clip_at") for r in rows]


async def rename(project_id: str, name: str) -> None:
    async with connection() as conn:
        await conn.execute("UPDATE projects SET name=%s WHERE id=%s", (name, project_id))
        await conn.commit()


async def delete(project_id: str) -> None:
    async with connection() as conn:
        await conn.execute("DELETE FROM projects WHERE id=%s", (project_id,))
        await conn.commit()


async def busy(project_id: str) -> bool:
    async with connection() as conn:
        row = await (await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM jobs WHERE project_id=%s"
            " AND status IN ('queued','running')) AS b", (project_id,))).fetchone()
    return bool(row["b"])


async def files_of(project_id: str) -> list[str]:
    async with connection() as conn:
        rows = await (await conn.execute(
            "SELECT output_key AS k FROM jobs WHERE project_id=%s AND output_key IS NOT NULL"
            " UNION ALL SELECT poster_key FROM jobs WHERE project_id=%s AND poster_key IS NOT NULL",
            (project_id, project_id))).fetchall()
    return [r["k"] for r in rows]


async def members(project_id: str) -> list[dict[str, Any]]:
    async with connection() as conn:
        return await (await conn.execute(
            "SELECT m.user_id::text AS user_id, u.email, m.role, u.avatar_key"
            " FROM project_members m JOIN users u ON u.id = m.user_id"
            " WHERE m.project_id=%s ORDER BY (m.role = 'owner') DESC, m.added_at",
            (project_id,))).fetchall()


async def add_member(project_id: str, user_id: str, role: str) -> bool:
    async with connection() as conn:
        cur = await conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (%s,%s,%s)"
            " ON CONFLICT DO NOTHING", (project_id, user_id, role))
        await conn.commit()
        return cur.rowcount == 1


async def set_role(project_id: str, user_id: str, role: str) -> bool:
    async with connection() as conn:
        cur = await conn.execute(
            "UPDATE project_members SET role=%s WHERE project_id=%s AND user_id=%s",
            (role, project_id, user_id))
        await conn.commit()
        return cur.rowcount == 1


async def remove_member(project_id: str, user_id: str) -> bool:
    async with connection() as conn:
        cur = await conn.execute(
            "DELETE FROM project_members WHERE project_id=%s AND user_id=%s",
            (project_id, user_id))
        await conn.commit()
        return cur.rowcount == 1


async def personal_for(user_id: str) -> str:
    """The project a clip lands in when nobody said which. Made on first use;
    the lock keeps two simultaneous first clips from making two."""
    async with connection() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext('personal:' || %s))",
                           (LOCK_NAMESPACE, user_id))
        row = await (await conn.execute(
            "SELECT id::text AS id FROM projects WHERE created_by=%s AND personal",
            (user_id,))).fetchone()
        if row is None:
            row = await (await conn.execute(
                "INSERT INTO projects (name, created_by, personal) VALUES (%s,%s,TRUE)"
                " RETURNING id::text AS id", (PERSONAL_NAME, user_id))).fetchone()
            await conn.execute(
                "INSERT INTO project_members (project_id, user_id, role) VALUES (%s,%s,'owner')",
                (row["id"], user_id))
        await conn.commit()
    return row["id"]


async def share_a_project(a: str, b: str) -> bool:
    async with connection() as conn:
        row = await (await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM project_members x JOIN project_members y"
            " ON x.project_id = y.project_id WHERE x.user_id=%s AND y.user_id=%s) AS s",
            (a, b))).fetchone()
    return bool(row["s"])
