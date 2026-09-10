"""The queue, from the browser's side. Every read is scoped to the caller."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import current_user
from ..estimate import estimate_batch
from ..store import jobs as jobs_store

router = APIRouter(prefix="/api", tags=["jobs"],
                   dependencies=[Depends(current_user)])

MAX_TAKES = 10
MODES = {"t2v", "i2v", "r2v"}


def split_prompts(text: str, mode: str | None) -> list[str]:
    """Turn the prompt box into one or more prompts.

    Single is the default because the two mistakes are not symmetrical: splitting
    a paragraph that happened to contain a line break silently produces extra
    clips from half-sentences and bills for them, while failing to split merely
    produces one job you can see and fix.
    """
    if (mode or "single") == "lines":
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = text.strip()
    return [text] if text else []


class NewJobs(BaseModel):
    prompts: str = ""
    split: str | None = None          # "single" (default) or "lines"
    seconds: int | None = None
    preset: str | None = None
    seed: int | None = None
    mode: str | None = None
    ref_images: list[str] = Field(default_factory=list)
    count: int = 1                    # takes per prompt, each with its own seed


class JobPatch(BaseModel):
    """Every field optional - the editor sends only what changed."""
    prompt: str | None = None
    seconds: int | None = None
    preset: str | None = None
    mode: str | None = None
    ref_images: list[str] | None = None


class AgainAllBody(BaseModel):
    status: str = "done"


def owned_keys(user_id: str, keys: list[str]) -> list[str]:
    """Keep only object keys inside this user's own prefix.

    The list arrives from the browser, so a key naming someone else's upload has
    to be dropped here rather than trusted into a job row.
    """
    prefix = f"uploads/{user_id}/"
    return [k for k in keys if k.startswith(prefix) and ".." not in k]


async def _mine_or_404(user: dict, job_id: str) -> dict[str, Any]:
    job = await jobs_store.get_for(user["id"], job_id)
    if job is None:
        # 404 rather than 403: a 403 would confirm that somebody else's job with
        # this id exists.
        raise HTTPException(404, "no such job")
    return job


@router.get("/jobs")
async def list_jobs(user: dict = Depends(current_user)) -> dict[str, Any]:
    return {"jobs": await jobs_store.list_for(user["id"])}


@router.post("/jobs")
async def add_jobs(body: NewJobs, request: Request,
                   user: dict = Depends(current_user)) -> dict[str, Any]:
    cfg = request.app.state.cfg
    prompts = split_prompts(body.prompts, body.split)
    if not prompts:
        raise HTTPException(400, "no prompts given")
    takes = max(1, min(MAX_TAKES, body.count))
    preset = body.preset or cfg.generation.default_preset
    if preset not in cfg.generation.presets:
        raise HTTPException(400, f"unknown preset {preset!r}")
    mode = body.mode or cfg.generation.default_mode
    if mode not in MODES:
        raise HTTPException(400, f"unknown mode {mode!r}")
    refs = owned_keys(user["id"], body.ref_images)
    seconds = max(4, min(15, body.seconds or cfg.generation.default_seconds))
    created: list[str] = []
    for prompt in prompts:
        for _ in range(takes):
            # Only pin the seed for a single take; several takes sharing one seed
            # would come out identical, which is never what "3 takes" means.
            seed = body.seed if (body.seed is not None and takes == 1) else None
            created.append(await jobs_store.add(
                user["id"], prompt[:2000], seconds=seconds, ref_images=refs,
                seed=seed, mode=mode, preset=preset))
    return {"created": created, "count": len(created)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    return await _mine_or_404(user, job_id)


@router.patch("/jobs/{job_id}")
async def patch_job(job_id: str, body: JobPatch, request: Request,
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """Edit a job.

    A running job is refused rather than silently edited: it is already on the
    GPU, so a change could not take effect and pretending otherwise would be
    worse than saying no. Editing a finished one puts it back in the queue,
    because the only reason to edit a finished job is to run it again.
    """
    cfg = request.app.state.cfg
    job = await _mine_or_404(user, job_id)
    if job["status"] == "running":
        raise HTTPException(409, "that job is generating right now - cancel it first")

    fields: dict[str, Any] = {}
    if body.prompt is not None:
        if not body.prompt.strip():
            raise HTTPException(400, "prompt cannot be empty")
        fields["prompt"] = body.prompt.strip()[:2000]
    if body.seconds is not None:
        fields["seconds"] = max(4, min(15, body.seconds))
    if body.preset is not None:
        if body.preset not in cfg.generation.presets:
            raise HTTPException(400, f"unknown preset {body.preset!r}")
        fields["preset"] = body.preset
    if body.mode is not None:
        if body.mode not in MODES:
            raise HTTPException(400, f"unknown mode {body.mode!r}")
        fields["mode"] = body.mode
    if body.ref_images is not None:
        fields["ref_images"] = owned_keys(user["id"], body.ref_images)

    requeued = job["status"] in {"done", "failed", "cancelled"}
    if requeued:
        fields.update(status="queued", attempts=0, error=None, remote_id=None,
                      output_key=None, finished_at=None)
    await jobs_store.update(job_id, **fields)
    return {"ok": True, "requeued": requeued,
            "job": await jobs_store.get_for(user["id"], job_id)}


@router.post("/jobs/again-all")
async def run_all_again(body: AgainAllBody,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """Re-queue every job of mine matching a status - the 'that batch again' case."""
    wanted = body.status or "done"
    if wanted not in {"done", "failed", "cancelled"}:
        raise HTTPException(400, "status must be done, failed or cancelled")
    created = 0
    for job in await jobs_store.list_for(user["id"]):
        if job["status"] != wanted:
            continue
        await jobs_store.add(user["id"], job["prompt"], seconds=job["seconds"],
                             ref_images=job["ref_images"], mode=job["mode"],
                             preset=job["preset"])
        created += 1
    return {"queued": created}


@router.post("/jobs/clear-finished")
async def clear_finished(user: dict = Depends(current_user)) -> dict[str, Any]:
    return {"removed": await jobs_store.clear_finished_for(user["id"])}


@router.post("/jobs/{job_id}/retry")
async def retry(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    await _mine_or_404(user, job_id)
    await jobs_store.update(job_id, status="queued", attempts=0, error=None,
                            remote_id=None, output_key=None)
    return {"ok": True}


@router.post("/jobs/{job_id}/again")
async def run_again(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """Queue a fresh copy of a finished job.

    A clone rather than a reset, so the earlier take and its file stay on record -
    re-rolling a prompt is how this is used, and comparing takes is the point. The
    seed is deliberately not copied: reusing it would reproduce the same clip.
    """
    job = await _mine_or_404(user, job_id)
    new_id = await jobs_store.add(
        user["id"], job["prompt"], seconds=job["seconds"],
        ref_images=job["ref_images"], mode=job["mode"], preset=job["preset"])
    return {"ok": True, "job_id": new_id}


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    await _mine_or_404(user, job_id)
    await jobs_store.update(job_id, status="cancelled")
    return {"ok": True}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """Remove one entry from the list.

    The stored clip is left alone: the queue is a work list, not the archive, and
    deleting a row should never destroy something the user waited and paid for.
    """
    job = await _mine_or_404(user, job_id)
    if job["status"] == "running":
        raise HTTPException(409, "that job is generating right now - cancel it first")
    await jobs_store.delete_for(user["id"], job_id)
    return {"ok": True}


@router.post("/estimate")
async def estimate(body: NewJobs, request: Request) -> dict[str, Any]:
    cfg = request.app.state.cfg
    prompts = split_prompts(body.prompts, body.split)
    clips = max(1, len(prompts)) * max(1, min(MAX_TAKES, body.count))
    preset = cfg.generation.preset(body.preset)
    seconds = body.seconds or cfg.generation.default_seconds
    gpu = cfg.runpod.gpu_preference[0] if cfg.runpod.gpu_preference else ""
    return {"clips": clips, **estimate_batch(gpu, clips, preset, seconds, cfg)}
