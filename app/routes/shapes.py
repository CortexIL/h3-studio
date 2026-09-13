"""What jobs and users look like to the browser.

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


def avatar_url(row: dict[str, Any]) -> str | None:
    key = row.get("avatar_key")
    if not key:
        return None
    # The key's random part versions the URL: a new picture is a new URL, so the
    # old one may stay cached forever without ever being shown again.
    version = key.rsplit("/", 1)[-1].split(".", 1)[0]
    return f"/api/avatar/{row['id']}?v={version}"


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    """A user row as the admin page sees it: a URL, never a storage key."""
    out = {k: v for k, v in row.items() if k not in ("avatar_key", "password_hash")}
    out["avatar_url"] = avatar_url(row)
    return out


def me_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Who the signed-in person is, as their own browser sees it."""
    return {"id": row["id"], "email": row["email"], "role": row["role"],
            "avatar_url": avatar_url(row)}


def public_job(row: dict[str, Any], queue_position: int | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "prompt": row["prompt"],
        "ref_images": row.get("ref_images") or [],
        "seconds": row["seconds"],
        "seed": row.get("seed"),
        "mode": row["mode"],
        "keep_audio": row.get("keep_audio"),
        "sound": row.get("sound"),
        "music": row.get("music"),
        "steps": row.get("steps"),
        "shift_video": row.get("shift_video"),
        "shift_audio": row.get("shift_audio"),
        "width": row.get("width"),
        "height": row.get("height"),
        "keyframes": row.get("keyframes") or [],
        "audio": row.get("audio_key"),
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
        "keep_audio": row.get("keep_audio"),
        "sound": row.get("sound"),
        "music": row.get("music"),
        "steps": row.get("steps"),
        "shift_video": row.get("shift_video"),
        "shift_audio": row.get("shift_audio"),
        "width": row.get("width"),
        "height": row.get("height"),
        "keyframes": row.get("keyframes") or [],
        "audio": row.get("audio_key"),
        "created_at": row.get("created_at"),
        "finished_at": row.get("finished_at"),
        "bytes": row.get("output_bytes"),
        **_urls(row),
    }
