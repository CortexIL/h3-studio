"""Job rows. Every read a route can reach is scoped to one user.

Two functions read a job: `get_for(user_id, job_id)` and `get_any(job_id)`. Routes
may only call the first. The second exists for the orchestrator, which processes
the global queue and legitimately has no user in hand - keeping them as separate
names means an unscoped read is visible at the call site rather than hidden in an
optional argument that someone will eventually leave out.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from .pool import connection

STATUSES = ("queued", "running", "done", "failed", "cancelled")

# How much of the feed one request carries, and the most a browser may ask for.
# The feed is polled every few seconds, so it is paged rather than sent whole;
# the page is generous because what it is a page of is usually a batch somebody
# has just queued and wants to watch.
FEED_LIMIT = 300
MAX_FEED_LIMIT = 2000

# Timestamps leave as float epoch seconds because the frontend, estimate.py and
# the orchestrator all already speak that; converting at the boundary is cheaper
# than changing three consumers.
COLUMNS = """
    id, user_id::text AS user_id, status, prompt, ref_images, seconds, seed, mode,
    preset,
    EXTRACT(EPOCH FROM created_at)  AS created_at,
    EXTRACT(EPOCH FROM started_at)  AS started_at,
    EXTRACT(EPOCH FROM finished_at) AS finished_at,
    attempts, error, output_key, output_bytes, remote_id, poster_key, keep_audio,
    sound, music, steps, shift_video, shift_audio, width, height, keyframes, audio_key,
    upscale_factor, source_frames, source_job_id, ref_videos, ref_audios, effects
"""

_TIMESTAMP_FIELDS = {"started_at", "finished_at"}


def _shape(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    for k in ("created_at", "started_at", "finished_at"):
        if row.get(k) is not None:
            row[k] = float(row[k])
    return row


def _encode(fields: dict[str, Any]) -> dict[str, Any]:
    """Translate the callers' vocabulary into values Postgres accepts."""
    out = dict(fields)
    if "ref_images" in out and not isinstance(out["ref_images"], str):
        out["ref_images"] = json.dumps(list(out["ref_images"]))
    if "keyframes" in out and not isinstance(out["keyframes"], str):
        out["keyframes"] = json.dumps(list(out["keyframes"]))
    for k in ("ref_videos", "ref_audios", "effects"):
        if k in out and not isinstance(out[k], str):
            out[k] = json.dumps(list(out[k]))
    for k in _TIMESTAMP_FIELDS:
        if k in out and isinstance(out[k], (int, float)):
            out[k] = datetime.fromtimestamp(out[k], tz=timezone.utc)
    return out


async def add(user_id: str, prompt: str, *, seconds: int = 10,
              ref_images: Iterable[str] = (), seed: int | None = None,
              mode: str = "i2v", preset: str = "final",
              keep_audio: bool | None = None, sound: str | None = None,
              music: str | None = None, steps: int | None = None,
              shift_video: float | None = None, shift_audio: float | None = None,
              width: int | None = None, height: int | None = None,
              keyframes: Iterable[dict[str, Any]] = (), audio_key: str | None = None,
              upscale_factor: int | None = None, source_frames: int | None = None,
              source_job_id: str | None = None, ref_videos: Iterable[str] = (),
              ref_audios: Iterable[str] = (), effects: Iterable[str] = ()) -> str:
    """Queue one clip. `keep_audio` of None means "whatever this install does";
    a None control means "whatever the preset says"."""
    job_id = uuid.uuid4().hex[:12]
    async with connection() as conn:
        await conn.execute(
            "INSERT INTO jobs (id, user_id, prompt, ref_images, seconds, seed, mode,"
            " preset, keep_audio, sound, music, steps, shift_video, shift_audio,"
            " width, height, keyframes, audio_key, upscale_factor, source_frames,"
            " source_job_id, ref_videos, ref_audios, effects, queue_pos)"
            " VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,"
            " %s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb, EXTRACT(EPOCH FROM now()))",
            (job_id, user_id, prompt, json.dumps(list(ref_images)), seconds, seed,
             mode, preset, keep_audio, sound or None, music or None, steps,
             shift_video, shift_audio, width, height, json.dumps(list(keyframes)),
             audio_key or None, upscale_factor, source_frames, source_job_id,
             json.dumps(list(ref_videos)), json.dumps(list(ref_audios)),
             json.dumps(list(effects))),
        )
        await conn.commit()
    return job_id


async def list_for(user_id: str, limit: int = FEED_LIMIT, *,
                   statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """A page of this user's feed, in the order the studio shows it.

    Capped, and deliberately not the whole story: what the page leaves out is
    reported by `feed_counts_for` instead. A capped list with no count beside
    it reads as a total, which is how a queue of two hundred and sixty could
    describe itself as exactly two hundred.
    """
    where = ""
    params: list[Any] = [user_id]
    if statuses is not None:
        wanted = list(statuses)
        if not wanted:
            return []
        where = " AND status = ANY(%s)"
        params.append(wanted)
    params.append(max(1, min(MAX_FEED_LIMIT, limit)))
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT {COLUMNS} FROM jobs WHERE user_id=%s AND dismissed_at IS NULL"
            f"{where}{_feed_order()} LIMIT %s", tuple(params)
        )).fetchall()
    return [_shape(r) for r in rows]


async def feed_counts_for(user_id: str) -> dict[str, int]:
    """How much work this user has, in the four words the studio's filters use.

    Counted in SQL, never by measuring the page above: the page has a ceiling
    and the count must not. Named apart from `counts_for` below, which counts
    every row this user owns by raw status, dismissed ones included - the feed
    is a different question and deserves a different name.
    """
    async with connection() as conn:
        rows = await (await conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs"
            " WHERE user_id=%s AND dismissed_at IS NULL GROUP BY status",
            (user_id,))).fetchall()
    by = {r["status"]: int(r["n"]) for r in rows}
    return {
        "all": sum(by.values()),
        "active": by.get("queued", 0) + by.get("running", 0),
        "ready": by.get("done", 0),
        "failed": by.get("failed", 0) + by.get("cancelled", 0),
    }


async def list_all(limit: int = 500) -> list[dict[str, Any]]:
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT {COLUMNS},"
            " (SELECT email FROM users u WHERE u.id = jobs.user_id) AS user_email"
            " FROM jobs ORDER BY created_at DESC LIMIT %s", (limit,)
        )).fetchall()
    return [_shape(r) for r in rows]


async def get_for(user_id: str, job_id: str) -> dict[str, Any] | None:
    async with connection() as conn:
        return _shape(await (await conn.execute(
            f"SELECT {COLUMNS} FROM jobs WHERE id=%s AND user_id=%s",
            (job_id, user_id))).fetchone())


async def get_any(job_id: str) -> dict[str, Any] | None:
    async with connection() as conn:
        return _shape(await (await conn.execute(
            f"SELECT {COLUMNS} FROM jobs WHERE id=%s", (job_id,))).fetchone())


async def update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    enc = _encode(fields)
    casts = {"ref_images": "%s::jsonb", "keyframes": "%s::jsonb",
             "ref_videos": "%s::jsonb", "ref_audios": "%s::jsonb", "effects": "%s::jsonb"}
    sets = ", ".join(f"{k}={casts.get(k, '%s')}" for k in enc)
    async with connection() as conn:
        await conn.execute(f"UPDATE jobs SET {sets} WHERE id=%s",
                           (*enc.values(), job_id))
        await conn.commit()


async def update_if(job_id: str, expect_status: str | tuple[str, ...],
                    **fields: Any) -> bool:
    """Update a job only if it is still in one of the expected states.

    The orchestrator and the routes race over the same rows - a user cancels
    while the render is finishing, or retries while it is being claimed. A plain
    UPDATE lets the last writer win and silently undo the other; this makes the
    state transition itself the guard, and tells the caller whether it won.
    """
    expect = [expect_status] if isinstance(expect_status, str) else list(expect_status)
    enc = _encode(fields)
    casts = {"ref_images": "%s::jsonb", "keyframes": "%s::jsonb",
             "ref_videos": "%s::jsonb", "ref_audios": "%s::jsonb", "effects": "%s::jsonb"}
    sets = ", ".join(f"{k}={casts.get(k, '%s')}" for k in enc)
    async with connection() as conn:
        cur = await conn.execute(
            f"UPDATE jobs SET {sets} WHERE id=%s AND status = ANY(%s)",
            (*enc.values(), job_id, expect))
        await conn.commit()
    return cur.rowcount > 0


async def dismiss_for(user_id: str, job_id: str) -> bool:
    """Hide a job from the studio feed. Its archive entry is untouched."""
    async with connection() as conn:
        cur = await conn.execute(
            "UPDATE jobs SET dismissed_at=now() WHERE id=%s AND user_id=%s",
            (job_id, user_id))
        await conn.commit()
    return cur.rowcount > 0


async def delete_for(user_id: str, job_id: str) -> bool:
    async with connection() as conn:
        cur = await conn.execute("DELETE FROM jobs WHERE id=%s AND user_id=%s",
                                 (job_id, user_id))
        await conn.commit()
    return cur.rowcount > 0


async def clear_finished_for(user_id: str) -> int:
    """Tidy the feed: hide finished clips, drop failed and cancelled jobs.

    Finished clips are hidden, never deleted - the archive is these rows, and
    this used to delete them, taking paid-for clips out of the archive while
    leaving their files orphaned in the bucket.
    """
    async with connection() as conn:
        hidden = await conn.execute(
            "UPDATE jobs SET dismissed_at=now() WHERE user_id=%s AND status='done'"
            " AND dismissed_at IS NULL", (user_id,))
        dropped = await conn.execute(
            "DELETE FROM jobs WHERE user_id=%s AND status IN ('failed','cancelled')",
            (user_id,))
        await conn.commit()
    return hidden.rowcount + dropped.rowcount


# One pod renders for everybody, so queue order is how the GPU gets shared out.
# Arrival order shares it badly: whoever queues forty clips first owns the pod for
# the rest of the evening and everyone behind them waits. A job's *turn* is instead
# its place in its own owner's backlog, and the pod serves turn 1 for every user
# before any user's turn 2. Two people with work outstanding therefore progress at
# roughly half speed each, instead of one at full speed and one at none.
#
# Arrival only breaks ties within a turn, so a single user's own jobs still run in
# the order they queued them, and with one user this is exactly the old FIFO.
#
# The backlog counts running jobs as well as queued ones, and that is the whole
# subtlety. Count only queued rows and a user's head always sits at turn 0 with the
# oldest timestamp, wins every tie-break, and arrival order comes straight back
# under a different name. Counting finished rows instead would swing too far the
# other way: yesterday's forty clips would be a debt that parks the user behind
# everyone forever. Queued-plus-running is the window that drains with the work.
_PENDING = "('queued','running')"


def _pos(alias: str) -> str:
    """The owner's own ordering key: where they dragged it, else when it arrived."""
    return f"COALESCE({alias}.queue_pos, EXTRACT(EPOCH FROM {alias}.created_at))"


def _turn(alias: str) -> str:
    """SQL for `alias`'s zero-based place in its own owner's pending backlog."""
    return (f"(SELECT COUNT(*) FROM jobs peer WHERE peer.status IN {_PENDING}"
            f" AND peer.user_id = {alias}.user_id"
            f" AND ({_pos('peer')}, peer.id) < ({_pos(alias)}, {alias}.id))")


def _feed_order() -> str:
    """The order one user's own feed is read in.

    Unfinished work first - what is rendering, then the queue in the order it
    will be rendered - and finished work after it, newest first. The order is
    only visible at the cap, and that is exactly where it matters: the rows a
    cap drops are then the far end of the queue and the oldest history, never
    the clip that is on the GPU right now.
    """
    return (f" ORDER BY (status IN {_PENDING}) DESC,"
            f" CASE WHEN status='queued' THEN {_pos('jobs')} END ASC NULLS FIRST,"
            " created_at DESC, id DESC")


# The same order as window functions, for the read-only position queries. A
# correlated count cannot be used there without an O(n^3) plan, and a window
# function cannot be used in the claim below because Postgres rejects FOR UPDATE
# alongside one. `test_queue_positions_agree_with_the_order_jobs_are_claimed_in`
# is what keeps the two spellings honest.
_FAIR_CTE = f"""
    WITH pending AS (
        SELECT id, user_id, status, {_pos('jobs')} AS pos
        FROM jobs WHERE status IN {_PENDING}
    ), ranked AS (
        SELECT id, user_id, status, pos,
               ROW_NUMBER() OVER (PARTITION BY user_id
                                  ORDER BY pos, id) AS turn
        FROM pending
    ), fair AS (
        SELECT id, user_id,
               ROW_NUMBER() OVER (ORDER BY turn, pos, id) - 1 AS ahead
        FROM ranked WHERE status='queued'
    )
"""


async def claim_next_queued() -> dict[str, Any] | None:
    """Take the next queued job in fair-share order, atomically.

    SKIP LOCKED rather than the SQLite version's BEGIN IMMEDIATE: it is the
    Postgres idiom for a work queue and it does not make a second claimer wait on
    the first, which matters because the caller is an event loop.
    """
    async with connection() as conn:
        row = await (await conn.execute(f"""
            UPDATE jobs SET status='running', started_at=now(), attempts=attempts+1
            WHERE id = (SELECT j.id FROM jobs j WHERE j.status='queued'
                        ORDER BY {_turn('j')}, {_pos('j')}, j.id
                        FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING {COLUMNS}
        """)).fetchone()
        await conn.commit()
    return _shape(row)


async def queued_shapes() -> list[dict[str, Any]]:
    """Preset and length of every queued job: how much GPU time the queue holds."""
    async with connection() as conn:
        return await (await conn.execute(
            "SELECT preset, seconds FROM jobs WHERE status='queued'")).fetchall()


async def _counts(where: str, params: tuple) -> dict[str, int]:
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT status, COUNT(*) AS c FROM jobs {where} GROUP BY status", params
        )).fetchall()
    out = {s: 0 for s in STATUSES}
    for r in rows:
        out[r["status"]] = int(r["c"])
    return out


async def counts_for(user_id: str) -> dict[str, int]:
    return await _counts("WHERE user_id=%s", (user_id,))


async def counts_all() -> dict[str, int]:
    return await _counts("", ())


async def queue_position(job_id: str) -> int:
    """How many queued jobs sit ahead of this one.

    A number only. Showing a user that three clips are in front of theirs is the
    difference between 'waiting through a cold boot' and 'broken'; showing them
    whose clips those are is not.
    """
    async with connection() as conn:
        row = await (await conn.execute(
            _FAIR_CTE + " SELECT ahead FROM fair WHERE id=%s", (job_id,))).fetchone()
    return int(row["ahead"]) if row else 0


async def reorder_for(user_id: str, ids: list[str]) -> int:
    """Set the order this user's queued jobs are claimed in. `ids` is first, last.

    The positions written are a permutation of the moved jobs' own existing
    positions rather than fresh numbers, so a drag keeps the block where it sat
    and cannot jump its owner ahead of anyone else: fair-share order counts each
    owner's backlog, and a permutation leaves that count unchanged.

    Scoped to one owner, and silently skips ids that are not their queued jobs -
    another user's id is not an error to report, it is simply not theirs to move,
    and the dispatcher may have claimed one mid-drag anyway.
    """
    if not ids:
        return 0
    async with connection() as conn:
        rows = await (await conn.execute(
            "SELECT id, COALESCE(queue_pos, EXTRACT(EPOCH FROM created_at)) AS pos"
            " FROM jobs WHERE user_id=%s AND status='queued' FOR UPDATE",
            (user_id,))).fetchall()
        mine = {r["id"]: float(r["pos"]) for r in rows}
        moving = [j for j in ids if j in mine]
        if not moving:
            await conn.commit()
            return 0
        # One statement rather than one per clip: a queue long enough to need
        # paging is long enough that a round trip per row makes a drag hang.
        await conn.execute(
            "UPDATE jobs SET queue_pos = v.pos"
            " FROM unnest(%s::text[], %s::float8[]) AS v(id, pos)"
            " WHERE jobs.id = v.id AND jobs.user_id=%s AND jobs.status='queued'",
            (moving, sorted(mine[j] for j in moving), user_id))
        await conn.commit()
    return len(moving)


async def queue_positions_for(user_id: str) -> dict[str, int]:
    """How many queued jobs sit ahead of each of this user's queued jobs.

    One query for the whole feed. Numbers only: the position says how long the
    wait is, never whose clips are in front.
    """
    async with connection() as conn:
        rows = await (await conn.execute(
            _FAIR_CTE + " SELECT id, ahead FROM fair WHERE user_id=%s",
            (user_id,))).fetchall()
    return {r["id"]: int(r["ahead"]) for r in rows}


USAGE_STATUSES = ("queued", "running", "done", "failed", "cancelled")


def empty_usage() -> dict[str, Any]:
    return {**{s: 0 for s in USAGE_STATUSES}, "stored_bytes": 0, "last_job_at": None}


async def usage_by_user() -> dict[str, dict[str, Any]]:
    """Per-user job counts and stored bytes, for the admin's users table.

    Clip counts only - GPU cost cannot be split per user, because a pod session
    renders everyone's jobs at once and runs carry no user.
    """
    async with connection() as conn:
        rows = await (await conn.execute(
            "SELECT user_id::text AS uid, status, COUNT(*) AS n,"
            " COALESCE(SUM(output_bytes) FILTER (WHERE status='done'), 0) AS bytes,"
            " EXTRACT(EPOCH FROM MAX(created_at)) AS last_at"
            " FROM jobs GROUP BY user_id, status")).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        u = out.setdefault(r["uid"], empty_usage())
        u[r["status"]] = int(r["n"])
        u["stored_bytes"] += int(r["bytes"])
        last = float(r["last_at"]) if r["last_at"] is not None else None
        if last is not None and (u["last_job_at"] is None or last > u["last_job_at"]):
            u["last_job_at"] = last
    return out


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _cursor_encode(finished_at: float, job_id: str) -> str:
    return base64.urlsafe_b64encode(f"{finished_at}|{job_id}".encode()).decode()


def _cursor_decode(cursor: str) -> tuple[float, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, jid = raw.split("|", 1)
        return float(ts), jid
    except (ValueError, UnicodeDecodeError):
        return None


# The archive is the finished clips that still have a file. Written once: the
# page, the count beside it and the sweep of ids behind "select all" have to mean
# the same thing, or "select all 500" selects a different 500.
ARCHIVE_WHERE = "WHERE user_id=%s AND status='done' AND output_key IS NOT NULL"

# The most ids one "select all" may carry. A ceiling rather than a promise: the
# reply says when it was reached, so the browser can say so too instead of
# quietly selecting some of them.
MAX_ARCHIVE_IDS = 2000


def _archive_filters(q: str | None, preset: str | None,
                     mode: str | None) -> tuple[str, list[Any]]:
    """The filters as plain extra WHERE clauses.

    Extra clauses rather than a rewritten query, so the keyset cursor keeps
    working unchanged inside a filtered result. The search text is escaped so a
    typed % or _ matches itself instead of everything.
    """
    sql = ""
    params: list[Any] = []
    if q and q.strip():
        sql += " AND prompt ILIKE %s ESCAPE '\\'"
        params.append(f"%{_escape_like(q.strip())}%")
    if preset:
        sql += " AND preset=%s"
        params.append(preset)
    if mode:
        sql += " AND mode=%s"
        params.append(mode)
    return sql, params


async def archive_count(user_id: str, *, q: str | None = None,
                        preset: str | None = None, mode: str | None = None) -> int:
    """How many clips match, whatever the page happens to hold."""
    extra, filters = _archive_filters(q, preset, mode)
    async with connection() as conn:
        row = await (await conn.execute(
            f"SELECT COUNT(*) AS n FROM jobs {ARCHIVE_WHERE}{extra}",
            tuple([user_id, *filters]))).fetchone()
    return int(row["n"]) if row else 0


async def archive_ids(user_id: str, *, q: str | None = None,
                      preset: str | None = None, mode: str | None = None,
                      limit: int = MAX_ARCHIVE_IDS) -> list[str]:
    """Every matching clip's id, in the order the archive shows them.

    Ids only: this exists so "select all" can mean all of them rather than the
    two dozen that happen to be on screen, and sending the rows themselves would
    be a megabyte of prompts to select a checkbox.
    """
    extra, filters = _archive_filters(q, preset, mode)
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT id FROM jobs {ARCHIVE_WHERE}{extra}"
            " ORDER BY finished_at DESC, id DESC LIMIT %s",
            tuple([user_id, *filters, max(1, min(MAX_ARCHIVE_IDS, limit))]))).fetchall()
    return [r["id"] for r in rows]


async def archive_page(user_id: str, cursor: str | None, limit: int = 24, *,
                       q: str | None = None, preset: str | None = None,
                       mode: str | None = None
                       ) -> tuple[list[dict[str, Any]], str | None]:
    """Finished clips, newest first, keyset-paginated.

    Keyset rather than OFFSET: a clip finishing while the user scrolls would shift
    every later page under an offset, showing one row twice and skipping another.
    A cursor that does not parse is treated as absent rather than as an error -
    the worst it can do is show the first page again.
    """
    limit = max(1, min(100, limit))
    extra, filters = _archive_filters(q, preset, mode)
    params: list[Any] = [user_id, *filters]
    if cursor and (decoded := _cursor_decode(cursor)):
        ts, jid = decoded
        extra += " AND (EXTRACT(EPOCH FROM finished_at), id) < (%s, %s)"
        params += [ts, jid]
    params.append(limit + 1)
    async with connection() as conn:
        rows = await (await conn.execute(
            f"SELECT {COLUMNS} FROM jobs {ARCHIVE_WHERE}{extra}"
            f" ORDER BY finished_at DESC, id DESC LIMIT %s", tuple(params)
        )).fetchall()
    rows = [_shape(r) for r in rows]
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = _cursor_encode(last["finished_at"] or 0.0, last["id"])
    return rows, next_cursor


async def requeue_stuck_running() -> int:
    """Anything left 'running' by a previous process is orphaned, not in flight."""
    async with connection() as conn:
        cur = await conn.execute(
            "UPDATE jobs SET status='queued', remote_id=NULL WHERE status='running'")
        await conn.commit()
    return cur.rowcount
