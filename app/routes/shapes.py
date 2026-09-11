"""What a job looks like to the browser.

The row carries storage keys, the backend's remote id and the owner's id. None
of that is the browser's business - a key in a payload is a key somebody will
try to fetch directly - so every route that returns a job goes through here and
the browser gets URLs instead.
"""
from __future__ import annotations

from typing import Any


def _urls(row: dict[str, Any]) -> dict[str, Any]:
    jid = row["id"]
    has_clip = row["status"] == "done" and bool(row.get("output_key"))
    return {
        "video_url": f"/api/video/{jid}" if has_clip else None,
        "poster_url": f"/api/poster/{jid}" if row.get("poster_key") else None,
    }


def public_job(row: dict[str, Any], queue_position: int | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "prompt": row["prompt"],
        "ref_images": row.get("ref_images") or [],
        "seconds": row["seconds"],
        "seed": row.get("seed"),
        "mode": row["mode"],
        "preset": row["preset"],
        "created_at": row.get("created_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "attempts": row.get("attempts", 0),
        "error": row.get("error"),
        "bytes": row.get("output_bytes"),
        "queue_position": queue_position if row["status"] == "queued" else None,
        **_urls(row),
    }


def public_clip(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "prompt": row["prompt"],
        "ref_images": row.get("ref_images") or [],
        "seconds": row["seconds"],
        "seed": row.get("seed"),
        "preset": row["preset"],
        "mode": row["mode"],
        "created_at": row.get("created_at"),
        "finished_at": row.get("finished_at"),
        "bytes": row.get("output_bytes"),
        **_urls(row),
    }
