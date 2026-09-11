"""Turning an uploaded batch file into jobs.

This was a folder watcher: files appeared on disk and a poll picked them up
exactly once, which is why it carried a seen-set, a wait window for images that
had not landed yet, and a replay buffer so two browser tabs would not race to
consume the same event. An upload has none of those problems - the whole file
arrives in one request, from one user, with the images inside it.

The preset named in a batch is passed through rather than checked here. The old
watcher validated against a hardcoded {"draft", "final"}, which silently rewrote
every `turbo` batch to draft after that preset was added; the route checks
against the presets the running config actually has.
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import PurePosixPath
from typing import Any

from . import storage as storage_mod

log = logging.getLogger("h3studio.batch")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
BATCH_SUFFIXES = {".txt", ".json"}
ARCHIVE_SUFFIXES = {".zip"}

MODES = {"t2v", "i2v", "r2v"}
MAX_JOBS_PER_BATCH = 200
MAX_ZIP_ENTRY_BYTES = 32 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 512 * 1024 * 1024


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _job(raw: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any] | None:
    merged = {**defaults, **raw}
    prompt = str(merged.get("prompt") or "").strip()
    if not prompt:
        return None
    images = (merged.get("images") or merged.get("image")
              or merged.get("reference") or [])
    if isinstance(images, str):
        images = [images]
    mode = str(merged.get("mode") or "").lower()
    preset = str(merged.get("preset") or "").lower()
    return {
        "prompt": prompt[:2000],
        "seconds": _clamp_int(merged.get("seconds"), 4, 15, 10),
        "takes": _clamp_int(merged.get("takes") or merged.get("count"), 1, 10, 1),
        "mode": mode if mode in MODES else None,
        "preset": preset or None,
        # Only bare filenames are honoured: a batch file is untrusted input and
        # must not be able to name something outside its own archive.
        "ref_names": [PurePosixPath(str(i)).name for i in images if str(i).strip()],
    }


def parse(text: str, suffix: str) -> list[dict[str, Any]]:
    """One batch file to a list of job descriptions.

    Raises ValueError with a message meant for the person who wrote the file.
    """
    if suffix == ".json":
        jobs = _parse_json(text)
    else:
        jobs = [_job({"prompt": line.strip()}, {})
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    jobs = [j for j in jobs if j]
    if len(jobs) > MAX_JOBS_PER_BATCH:
        raise ValueError(f"that batch has {len(jobs)} prompts; the limit is "
                         f"{MAX_JOBS_PER_BATCH}")
    return jobs


def _parse_json(text: str) -> list[dict[str, Any] | None]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e}") from e

    # Accept a bare list of jobs, or of plain strings, as well as the documented
    # {"defaults":..., "jobs":[...]} shape. Exporters are inconsistent and there
    # is no reason to reject a form whose intent is unambiguous.
    if isinstance(data, list):
        raw, defaults = data, {}
    elif isinstance(data, dict):
        raw = data.get("jobs") or data.get("prompts") or []
        defaults = data.get("defaults") or {}
        if not isinstance(raw, list):
            raise ValueError("'jobs' must be a list")
        if not isinstance(defaults, dict):
            defaults = {}
    else:
        raise ValueError("expected an object or a list")

    out = []
    for item in raw:
        if isinstance(item, str):
            item = {"prompt": item}
        if not isinstance(item, dict):
            continue
        out.append(_job(item, defaults))
    return out


async def unpack_zip(data: bytes, user_id: str,
                     storage: Any) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Read one archive: images to the object store, batch files to jobs.

    Entry names are reduced to their basename before anything is stored, because
    a zip is allowed to contain `../../etc/passwd` and this one arrived from a
    browser.
    """
    jobs: list[dict[str, Any]] = []
    images: dict[str, str] = {}
    total = 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"that .zip could not be opened: {e}") from e

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename.replace("\\", "/")).name
            suffix = PurePosixPath(name).suffix.lower()
            if info.file_size > MAX_ZIP_ENTRY_BYTES:
                log.warning("skipping oversized zip entry %s", name)
                continue
            # Checked against the declared size before reading, so a zip bomb is
            # refused rather than decompressed first and refused afterwards.
            total += info.file_size
            if total > MAX_ZIP_TOTAL_BYTES:
                raise ValueError("that archive unpacks to more than 512 MB")
            if suffix in IMAGE_SUFFIXES:
                key = storage_mod.upload_key(user_id, suffix)
                await storage.put(key, zf.read(info), "application/octet-stream")
                images[name] = key
            elif suffix in BATCH_SUFFIXES:
                jobs += parse(zf.read(info).decode("utf-8-sig", "replace"), suffix)
    return jobs, images
