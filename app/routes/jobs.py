"""The queue, from the browser's side. Every read is scoped to the caller."""
from __future__ import annotations

import time
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import controls
from .. import storage as storage_mod
from ..auth import current_user
from ..sinks import tail_clip_bytes, video_frame_count
from ..estimate import estimate_batch
from .. import effects as effects_mod
from ..modes import OFFERED as MODES
from ..modes import REF_COUNTS, REF_ERRORS, without_picture
from ..store import jobs as jobs_store
from .shapes import public_job

router = APIRouter(prefix="/api", tags=["jobs"],
                   dependencies=[Depends(current_user)])

MAX_TAKES = 10
# A ceiling on how many clips one press can queue. Re-running a selection is the
# cheapest way in this whole app to spend a lot of money by accident.
MAX_AGAIN = 200


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
    # None leaves it to the install's own setting, which is what every clip
    # queued before the composer had a sound switch was rendered under.
    keep_audio: bool | None = None
    # Sound direction, kept apart from the shot: H3 reads a separate soundscape
    # and music description far better than sound words inside the prompt.
    sound: str | None = None
    music: str | None = None
    # Render controls on top of the preset. None = the preset's own value.
    steps: int | None = None
    shift_video: float | None = None
    shift_audio: float | None = None
    width: int | None = None
    height: int | None = None
    # Images pinned at a moment inside the clip, in seconds from its start.
    keyframes: list["Keyframe"] = Field(default_factory=list)
    # A voice or audio track the clip must follow (an upload key).
    audio: str | None = None
    # References mode only: reference videos and standalone audio clips.
    ref_videos: list[str] = Field(default_factory=list)
    ref_audios: list[str] = Field(default_factory=list)
    # Effect presets by name (app/effects.py); unknown names are dropped, not refused.
    effects: list[str] = Field(default_factory=list)


MAX_REF_MEDIA = 3


def _reference_media(user_id: str, body: "NewJobs", mode: str,
                     images: list[str]) -> tuple[list[str], list[str]]:
    """The reference videos and audio a References job carries."""
    videos = owned_keys(user_id, body.ref_videos)[:MAX_REF_MEDIA]
    audios = owned_keys(user_id, body.ref_audios)[:MAX_REF_MEDIA]
    if mode != "r2v":
        if videos or audios:
            raise HTTPException(400, "reference videos and audio are for the References mode")
        return [], []
    if not (images or videos or audios):
        raise HTTPException(400, "references need at least one image, video or audio clip")
    if body.keyframes or body.audio:
        raise HTTPException(400, "references cannot be combined with keyframes or an "
                                 "audio track to follow - use them as references instead")
    return videos, audios


def _audio_guide(user_id: str, body: "NewJobs", mode: str) -> str | None:
    if not body.audio:
        return None
    if not owned_keys(user_id, [body.audio]):
        raise HTTPException(400, "that audio track is not one of yours")
    if mode == "extend":
        raise HTTPException(400, "extend already carries the sound of the clip it continues; "
                                 "an audio track cannot be anchored on top of it")
    if body.keep_audio is False:
        raise HTTPException(400, "an audio track needs the sound switch on, or it would be "
                                 "stripped from the finished clip")
    return body.audio


class Keyframe(BaseModel):
    key: str
    at: float


MAX_KEYFRAMES = 6
# Two anchors closer than this fight over the same frames.
MIN_KEYFRAME_GAP = 0.25


def _keyframes(user_id: str, body: "NewJobs", seconds: int) -> list[dict[str, Any]]:
    """The clip's keyframes: owned keys only, strictly inside the clip, in order."""
    if len(body.keyframes) > MAX_KEYFRAMES:
        raise HTTPException(400, f"at most {MAX_KEYFRAMES} keyframes")
    owned = set(owned_keys(user_id, [k.key for k in body.keyframes]))
    kept = sorted(({"key": k.key, "at": round(float(k.at), 2)}
                   for k in body.keyframes if k.key in owned), key=lambda k: k["at"])
    for k in kept:
        # 0 is the start frame's job and the last frame is Start to end's.
        if not 0 < k["at"] < seconds:
            raise HTTPException(400, f"a keyframe must sit inside the clip: 0 < at < {seconds}")
    for a, b in zip(kept, kept[1:]):
        if b["at"] - a["at"] < MIN_KEYFRAME_GAP:
            raise HTTPException(400, "keyframes need at least a quarter second between them")
    return kept


def _controls(body: "NewJobs") -> dict[str, Any]:
    """The clip's overrides, validated, as keyword arguments for the store."""
    try:
        controls.validate(body.steps, body.shift_video, body.shift_audio,
                          body.width, body.height)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"steps": body.steps, "shift_video": body.shift_video,
            "shift_audio": body.shift_audio, "width": body.width, "height": body.height}


class JobPatch(BaseModel):
    """Every field optional - the editor sends only what changed."""
    prompt: str | None = None
    seconds: int | None = None
    preset: str | None = None
    mode: str | None = None
    ref_images: list[str] | None = None


class AgainAllBody(BaseModel):
    status: str = "done"


class AgainMany(BaseModel):
    """The clips to queue fresh takes of."""
    ids: list[str] = Field(default_factory=list)


def owned_keys(user_id: str, keys: list[str]) -> list[str]:
    """Keep only object keys inside this user's own prefix.

    The list arrives from the browser, so a key naming someone else's upload has
    to be dropped here rather than trusted into a job row.
    """
    prefix = f"uploads/{user_id}/"
    return [k for k in keys if k.startswith(prefix) and ".." not in k]


def check_refs(mode: str, refs: list[str]) -> None:
    """Refuse a mode whose inputs are missing, rather than rendering the wrong thing.

    Counted after owned_keys(), deliberately. That filter *drops* a key belonging
    to someone else rather than refusing it, so a start-to-end job sent with
    another person's end frame would arrive here with a single image - and bind it
    as the start frame. Counting afterwards turns that into a 400 instead of a clip
    nobody asked for.
    """
    span = REF_COUNTS.get(mode)
    if span is not None and not (span[0] <= len(refs) <= span[1]):
        raise HTTPException(400, REF_ERRORS[mode])


def _direction(text: str | None) -> str | None:
    """A sound or music description, or None when the field was left empty."""
    cleaned = (text or "").strip()[:1000]
    return cleaned or None


async def _mine_or_404(user: dict, job_id: str) -> dict[str, Any]:
    job = await jobs_store.get_for(user["id"], job_id)
    if job is None:
        # 404 rather than 403: a 403 would confirm that somebody else's job with
        # this id exists.
        raise HTTPException(404, "no such job")
    return job


@router.get("/jobs")
async def list_jobs(limit: int = Query(default=jobs_store.FEED_LIMIT, ge=1,
                                       le=jobs_store.MAX_FEED_LIMIT),
                    user: dict = Depends(current_user)) -> dict[str, Any]:
    """A page of this caller's feed, and the true size of the whole of it.

    Paged because this is polled every few seconds and an unbounded feed would
    be sent again and again. `counts` is what stops the page pretending to be
    the total: the browser can say "300 of 812" and ask for the rest, instead of
    quietly showing a number that is really just the ceiling.
    """
    rows = await jobs_store.list_for(user["id"], limit)
    positions = await jobs_store.queue_positions_for(user["id"])
    counts = await jobs_store.feed_counts_for(user["id"])
    return {"jobs": [public_job(r, positions.get(r["id"])) for r in rows],
            "counts": counts, "shown": len(rows), "limit": limit,
            "has_more": len(rows) < counts["all"]}


async def _shaped(job: dict[str, Any]) -> dict[str, Any]:
    position = (await jobs_store.queue_position(job["id"])
                if job["status"] == "queued" else None)
    return public_job(job, position)


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
    mode = without_picture(mode, refs)
    check_refs(mode, refs)
    seconds = max(4, min(15, body.seconds or cfg.generation.default_seconds))
    knobs = _controls(body)
    ref_videos, ref_audios = _reference_media(user["id"], body, mode, refs)
    keyframes = _keyframes(user["id"], body, seconds)
    audio_key = _audio_guide(user["id"], body, mode)
    created: list[str] = []
    for prompt in prompts:
        for _ in range(takes):
            # Only pin the seed for a single take; several takes sharing one seed
            # would come out identical, which is never what "3 takes" means.
            seed = body.seed if (body.seed is not None and takes == 1) else None
            created.append(await jobs_store.add(
                user["id"], prompt[:2000], seconds=seconds, ref_images=refs,
                seed=seed, mode=mode, preset=preset, keep_audio=body.keep_audio,
                sound=_direction(body.sound), music=_direction(body.music),
                keyframes=keyframes, audio_key=audio_key, ref_videos=ref_videos,
                ref_audios=ref_audios, effects=effects_mod.clean(body.effects), **knobs))
    return {"created": created, "count": len(created)}


class UpscaleBody(BaseModel):
    # "2x": twice the render size. "1080p": that, conformed to an exact 1920x1080.
    deliver: str = "2x"


UPSCALE_PRESETS = {"2x": "up2x", "1080p": "hd1080up"}


@router.post("/jobs/{job_id}/upscale")
async def upscale(job_id: str, body: UpscaleBody, request: Request,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """Enlarge a finished clip: a new job whose input is the clip itself.

    Twice the size through Real-ESRGAN on the same pod, frame by frame, the
    sound carried across. The source stays exactly as it was.
    """
    preset = UPSCALE_PRESETS.get(body.deliver)
    if preset is None:
        raise HTTPException(400, "deliver must be 2x or 1080p")
    src = await _mine_or_404(user, job_id)
    if src["status"] != "done" or not src.get("output_key"):
        raise HTTPException(404, "that clip has nothing to upscale yet")
    if src.get("mode") == "upscale":
        raise HTTPException(400, "that clip is already an upscale")
    try:
        data = await request.app.state.storage.get(src["output_key"])
    except storage_mod.ObjectMissing:
        raise HTTPException(404, "that clip's file is no longer stored")
    frames = await run_in_threadpool(video_frame_count, data)
    new_id = await jobs_store.add(
        user["id"], f"Upscale ×2 · {src['prompt']}"[:2000], seconds=src["seconds"],
        ref_images=[src["output_key"]], mode="upscale", preset=preset,
        keep_audio=src.get("keep_audio"), upscale_factor=2, source_frames=frames,
        source_job_id=src["id"])
    return {"ok": True, "job_id": new_id, "frames": frames}


@router.post("/jobs/{job_id}/extend-source")
async def extend_source(job_id: str, request: Request,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """Cut the tail off one of my finished clips, ready for a job to continue it.

    The tail is stored as a new upload of the caller's own, so the job that
    continues it carries an `uploads/{me}/` key like every other input and
    owned_keys stays the single place a browser-supplied key is judged. Copying
    rather than pointing at the archived clip also means deleting that clip later
    cannot break a job still waiting in the queue.

    The cut happens here rather than at dispatch on purpose: this leaves the
    orchestrator - the one loop that owns a billing GPU - carrying about a second
    of video instead of a whole clip.
    """
    job = await _mine_or_404(user, job_id)
    if job["status"] != "done" or not job.get("output_key"):
        raise HTTPException(404, "that clip has no video to continue")

    store = request.app.state.storage
    try:
        data = await store.get(job["output_key"])
    except storage_mod.ObjectMissing:
        raise HTTPException(404, "that clip's video is gone") from None

    tail = await run_in_threadpool(tail_clip_bytes, data)
    if tail is None:
        raise HTTPException(400, "that clip could not be read")

    key = storage_mod.upload_key(user["id"], ".mp4")
    await store.put(key, tail, "video/mp4")
    # The preset comes back so the new clip can be rendered at the size the old
    # one was: the guide is centre-cropped to fit, so a mismatch would continue a
    # cropped version of the source.
    return {"key": key, "name": PurePosixPath(key).name,
            "preset": job["preset"], "seconds": job["seconds"]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    return await _shaped(await _mine_or_404(user, job_id))


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

    # A patch may change the mode, the references, or only one of them, so this has
    # to judge the row as it will be afterwards rather than what arrived.
    refs = fields.get("ref_images")
    check_refs(fields.get("mode", job.get("mode")),
               list(job.get("ref_images") or []) if refs is None else refs)

    requeued = job["status"] in {"done", "failed", "cancelled"}
    if requeued:
        fields.update(status="queued", attempts=0, error=None, remote_id=None,
                      output_key=None, finished_at=None)
    await jobs_store.update(job_id, **fields)
    return {"ok": True, "requeued": requeued,
            "job": await _shaped(await jobs_store.get_for(user["id"], job_id))}


@router.post("/jobs/again-all")
async def run_all_again(body: AgainAllBody,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """Re-queue every job of mine matching a status - the 'that batch again' case.

    Asked of the store by status rather than by filtering the feed page, which
    is capped: filtering a page re-ran "everything" that happened to be on the
    first screen and silently skipped the rest. The same MAX_AGAIN ceiling as a
    hand-picked selection still applies - this spends money - and the reply says
    whether that ceiling is what stopped it.
    """
    wanted = body.status or "done"
    if wanted not in {"done", "failed", "cancelled"}:
        raise HTTPException(400, "status must be done, failed or cancelled")
    created = 0
    matching = await jobs_store.list_for(user["id"], MAX_AGAIN, statuses=(wanted,))
    for job in matching:
        await jobs_store.add(user["id"], job["prompt"], seconds=job["seconds"],
                             ref_images=job["ref_images"], mode=job["mode"],
                             preset=job["preset"], keep_audio=job.get("keep_audio"),
            sound=job.get("sound"), music=job.get("music"),
            keyframes=job.get("keyframes") or [], audio_key=job.get("audio_key"),
            upscale_factor=job.get("upscale_factor"), source_frames=job.get("source_frames"),
            source_job_id=job.get("source_job_id"),
            ref_videos=job.get("ref_videos") or [], ref_audios=job.get("ref_audios") or [],
            effects=job.get("effects") or [],
            **{k: job.get(k) for k in controls.FIELDS})
        created += 1
    # Whether the ceiling is what stopped it, rather than a guess at how many
    # are left: the caller can press again, and a wrong number would be worse
    # than none.
    return {"queued": created, "capped": len(matching) == MAX_AGAIN}


@router.post("/jobs/clear-finished")
async def clear_finished(user: dict = Depends(current_user)) -> dict[str, Any]:
    return {"removed": await jobs_store.clear_finished_for(user["id"])}


class QueueOrder(BaseModel):
    """Queued job ids, first to render, last."""
    ids: list[str] = Field(default_factory=list)


# A queue this long is a mis-send rather than a drag; the store would happily
# take it, but there is no reason to read an unbounded list off the wire. It
# tracks the feed's own ceiling: the browser sends the order of every waiting
# clip it has, so anything it can show it must also be able to reorder.
MAX_REORDER = jobs_store.MAX_FEED_LIMIT


@router.post("/jobs/order")
async def reorder_queue(body: QueueOrder,
                        user: dict = Depends(current_user)) -> dict[str, Any]:
    """Set the order this caller's queued clips are rendered in.

    `ids` arrives in render order, first to last; the browser holds the reversal
    because its feed reads newest-first while the queue drains oldest-first.

    Ids that are not this user's queued jobs are skipped rather than refused.
    The dispatcher may have claimed one while the drag was in flight, and
    another user's id is simply not theirs to move - which is also why the reply
    counts what moved instead of naming what did not.
    """
    if not body.ids:
        raise HTTPException(400, "no job ids given")
    moved = await jobs_store.reorder_for(user["id"], body.ids[:MAX_REORDER])
    return {"ok": True, "reordered": moved}


@router.post("/jobs/{job_id}/retry")
async def retry(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """Put a failed or cancelled job back in the queue.

    Refused for a running job (it is on the GPU) and for a finished one:
    retrying a finished job used to wipe its output_key and drop a paid-for clip
    out of the archive. Re-rolling a finished prompt is what "again" is for.
    """
    await _mine_or_404(user, job_id)
    ok = await jobs_store.update_if(
        job_id, ("failed", "cancelled"), status="queued", attempts=0, error=None,
        remote_id=None, output_key=None, started_at=None, finished_at=None)
    if not ok:
        raise HTTPException(409, "only failed or cancelled jobs can be retried")
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
        ref_images=job["ref_images"], mode=job["mode"], preset=job["preset"],
        keep_audio=job.get("keep_audio"),
            sound=job.get("sound"), music=job.get("music"),
            keyframes=job.get("keyframes") or [], audio_key=job.get("audio_key"),
            upscale_factor=job.get("upscale_factor"), source_frames=job.get("source_frames"),
            source_job_id=job.get("source_job_id"),
            ref_videos=job.get("ref_videos") or [], ref_audios=job.get("ref_audios") or [],
            effects=job.get("effects") or [],
            **{k: job.get(k) for k in controls.FIELDS})
    return {"ok": True, "job_id": new_id}


@router.post("/jobs/again")
async def run_many_again(body: AgainMany,
                         user: dict = Depends(current_user)) -> dict[str, Any]:
    """Queue a fresh take of several clips at once.

    Ids that are not the caller's own are skipped rather than refused. A
    selection is a list of things somebody pointed at, and one clip deleted in
    another tab should not cost them the other twenty-nine - the same reasoning
    the bulk download follows.

    As with a single re-run, the seed is deliberately not copied: reusing it
    would reproduce the clip they already have.
    """
    if not body.ids:
        raise HTTPException(400, "no clips given")
    queued: list[str] = []
    for job_id in list(dict.fromkeys(body.ids))[:MAX_AGAIN]:
        job = await jobs_store.get_for(user["id"], job_id)
        if job is None:
            continue
        queued.append(await jobs_store.add(
            user["id"], job["prompt"], seconds=job["seconds"],
            ref_images=job["ref_images"], mode=job["mode"], preset=job["preset"],
            keep_audio=job.get("keep_audio"),
            sound=job.get("sound"), music=job.get("music"),
            keyframes=job.get("keyframes") or [], audio_key=job.get("audio_key"),
            upscale_factor=job.get("upscale_factor"), source_frames=job.get("source_frames"),
            source_job_id=job.get("source_job_id"),
            ref_videos=job.get("ref_videos") or [], ref_audios=job.get("ref_audios") or [],
            effects=job.get("effects") or [],
            **{k: job.get(k) for k in controls.FIELDS}))
    if not queued:
        raise HTTPException(404, "none of those clips are available")
    return {"queued": len(queued), "created": queued}


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """Cancel a queued or running job.

    Guarded on the current state in one statement, so it cannot race the queue
    claim. A finished job is refused: cancelling it used to flip a done clip to
    cancelled and take it out of the archive.
    """
    await _mine_or_404(user, job_id)
    ok = await jobs_store.update_if(job_id, ("queued", "running"),
                                    status="cancelled", finished_at=time.time())
    if not ok:
        raise HTTPException(409, "that job has already finished")
    return {"ok": True}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    """Remove one entry from the list.

    A finished clip is only hidden from the feed: the archive is these same
    rows, so deleting it would take something the user waited and paid for out
    of their archive. Anything that never produced a clip is deleted outright.
    """
    job = await _mine_or_404(user, job_id)
    if job["status"] == "running":
        raise HTTPException(409, "that job is generating right now - cancel it first")
    if job["status"] == "done" and job.get("output_key"):
        await jobs_store.dismiss_for(user["id"], job_id)
        return {"ok": True, "hidden": True}
    await jobs_store.delete_for(user["id"], job_id)
    return {"ok": True, "hidden": False}


@router.post("/estimate")
async def estimate(body: NewJobs, request: Request) -> dict[str, Any]:
    cfg = request.app.state.cfg
    prompts = split_prompts(body.prompts, body.split)
    clips = max(1, len(prompts)) * max(1, min(MAX_TAKES, body.count))
    # Priced on what will actually render: the preset with the clip's overrides.
    preset = controls.effective(cfg.generation.preset(body.preset), _controls(body))
    seconds = body.seconds or cfg.generation.default_seconds
    gpu = cfg.runpod.gpu_preference[0] if cfg.runpod.gpu_preference else ""
    max_pods = await request.app.state.orch.max_pods()
    return {"clips": clips,
            **estimate_batch(gpu, clips, preset, seconds, cfg, max_pods=max_pods)}
