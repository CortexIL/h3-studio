"""HTTP server: JSON API + the local web page.

The page is a dumb client of this API - no logic, no secrets. That is the single
decision that makes the move to a shared team instance cheap: the same process runs
on a $5 VPS, five browsers point at it, and nothing here changes except the bind
address and adding a login.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config as config_mod
from . import db
from .estimate import estimate_batch
from .inbox import ARCHIVE_SUFFIXES, BATCH_SUFFIXES, IMAGE_SUFFIXES, Inbox
from .orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
UPLOADS = ROOT / "data" / "uploads"

log = logging.getLogger("h3studio")


class _RecentEvents:
    """Short replay buffer for inbox pickups.

    The inbox reports each file exactly once, which is right for queuing but wrong for
    a polling UI: with two browser tabs open, whichever polls first consumes the event
    and the other never sees it - so a picked-up image attaches nowhere. Holding the
    last few seconds of events and letting clients dedupe (they key on path) makes the
    display idempotent while queuing stays strictly once.
    """

    WINDOW_SECONDS = 45

    def __init__(self) -> None:
        self._items: list[tuple[float, str, dict[str, Any]]] = []

    def add(self, kind: str, entries: list[dict[str, Any]]) -> None:
        now = time.time()
        self._items += [(now, kind, e) for e in entries]

    def get(self, kind: str) -> list[dict[str, Any]]:
        cutoff = time.time() - self.WINDOW_SECONDS
        self._items = [i for i in self._items if i[0] > cutoff]
        return [e for _, k, e in self._items if k == kind]


def _queue_inbox_batches(cfg: config_mod.Config,
                         batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn parsed inbox batches into queued jobs.

    Queuing is what "drop a batch file in" is meant to do, and the existing pod policy
    still governs whether a GPU actually starts - with policy 'off' these simply sit
    in the queue. The budget ceiling remains the backstop either way.
    """
    results: list[dict[str, Any]] = []
    for batch in batches:
        if batch.get("error"):
            results.append({"source": batch["source"], "queued": 0,
                            "error": batch["error"]})
            continue
        created = 0
        for job in batch["jobs"]:
            for _ in range(job["takes"]):
                db.add_job(
                    job["prompt"],
                    seconds=job["seconds"],
                    ref_images=job["ref_images"],
                    mode=job["mode"],
                    preset=job["preset"],
                    owner="inbox",
                )
                created += 1
        results.append({
            "source": batch["source"],
            "queued": created,
            "missing_images": batch.get("missing") or [],
        })
        log.info("inbox: queued %d job(s) from %s", created, batch["source"])
    return results


def _reveal(folder: Path) -> None:
    """Open a folder in the OS file manager."""
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as e:
        raise HTTPException(500, f"could not open folder: {e}")


def make_backend(cfg: config_mod.Config):
    if cfg.mock:
        from .backends.mock import MockBackend
        return MockBackend(cfg)
    if not cfg.runpod.api_key or cfg.runpod.api_key.startswith("YOUR_"):
        raise SystemExit(
            "No RunPod API key. Either copy config.example.yaml to config.yaml and fill in\n"
            "runpod.api_key, set RUNPOD_API_KEY in the environment, or run with --mock to\n"
            "try the whole pipeline for free."
        )
    from .backends.runpod_pod import RunpodBackend
    return RunpodBackend(cfg)


# ---------- request models ----------

class NewJobs(BaseModel):
    # One prompt per line is the fastest way to load a batch of 40.
    prompts: str = ""
    seconds: int | None = None
    preset: str | None = None
    seed: int | None = None
    mode: str = "t2v"
    ref_images: list[str] = Field(default_factory=list)
    count: int = 1                     # takes per prompt, each with its own seed


class PolicyBody(BaseModel):
    policy: str


class FolderBody(BaseModel):
    folder: str


class KeyBody(BaseModel):
    key: str


class AgainAllBody(BaseModel):
    status: str = "done"


class JobPatch(BaseModel):
    """Every field optional - the editor sends only what changed."""
    prompt: str | None = None
    seconds: int | None = None
    preset: str | None = None
    mode: str | None = None
    ref_images: list[str] | None = None


def create_app(cfg: config_mod.Config) -> FastAPI:
    orch = Orchestrator(cfg, make_backend(cfg))
    inbox = Inbox(Path(cfg.output.inbox), UPLOADS)
    recent = _RecentEvents()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        UPLOADS.mkdir(parents=True, exist_ok=True)
        inbox.ensure()
        await orch.start()
        try:
            yield
        finally:
            # Runs on Ctrl-C too. A pod outliving the process is the one failure
            # mode here that costs real money.
            await orch.stop()

    app = FastAPI(title="H3 Studio", docs_url="/api/docs", lifespan=lifespan)
    app.state.cfg = cfg
    app.state.orch = orch


    # ---------- status ----------

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        # Scanned on the status poll rather than on a timer of its own: the UI is
        # already asking every two seconds, and this keeps it to one moving part.
        found = await asyncio.to_thread(inbox.scan)
        # Queue once, from this tick's take; replay to clients for a few seconds.
        recent.add("image", found["images"])
        recent.add("batch", _queue_inbox_batches(cfg, found["batches"]))
        return {
            **orch.snapshot(),
            "config": cfg.public(),
            "inbox_folder": str(inbox.folder),
            "inbox_new": recent.get("image"),
            "inbox_batches": recent.get("batch"),
            "inbox_waiting": found.get("waiting_for_images") or [],
            "has_api_key": bool(cfg.runpod.api_key
                                and not cfg.runpod.api_key.startswith("YOUR_")),
            "api_key_hint": (cfg.runpod.api_key[-4:]
                             if cfg.runpod.api_key
                             and not cfg.runpod.api_key.startswith("YOUR_") else ""),
        }

    @app.get("/api/runs")
    async def runs() -> dict[str, Any]:
        rows = db.recent_runs()
        return {"runs": rows, "total_cost_usd": round(sum(r["cost_estimate"] for r in rows), 4)}

    # ---------- jobs ----------

    @app.get("/api/jobs")
    async def jobs() -> dict[str, Any]:
        return {"jobs": db.list_jobs()}

    @app.post("/api/jobs")
    async def add_jobs(body: NewJobs) -> dict[str, Any]:
        prompts = [p.strip() for p in body.prompts.splitlines() if p.strip()]
        if not prompts:
            raise HTTPException(400, "no prompts given")
        takes = max(1, min(10, body.count))
        preset = body.preset or cfg.generation.default_preset
        if preset not in cfg.generation.presets:
            raise HTTPException(400, f"unknown preset {preset!r}")
        created: list[str] = []
        for prompt in prompts:
            for _ in range(takes):
                # Only pin the seed for a single take; several takes sharing one seed
                # would come out identical, which is never what "3 takes" means.
                seed = body.seed if (body.seed is not None and takes == 1) else None
                created.append(db.add_job(
                    prompt,
                    seconds=body.seconds or cfg.generation.default_seconds,
                    ref_images=body.ref_images,
                    seed=seed,
                    mode=body.mode,
                    preset=preset,
                ))
        return {"created": created, "count": len(created)}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        # The browser cannot read local paths, so hand it names it can fetch back
        # through /api/image/ instead of the absolute paths stored on the job.
        job["ref_names"] = [Path(p).name for p in job.get("ref_images") or []]
        return job

    @app.patch("/api/jobs/{job_id}")
    async def patch_job(job_id: str, body: JobPatch) -> dict[str, Any]:
        """Edit a job.

        A running job is refused rather than silently edited: it is already on the
        GPU, so a change could not take effect and pretending otherwise would be
        worse than saying no. Editing a finished one puts it back in the queue,
        because the only reason to edit a finished job is to run it again.
        """
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "no such job")
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
            if body.mode not in {"t2v", "i2v", "r2v"}:
                raise HTTPException(400, f"unknown mode {body.mode!r}")
            fields["mode"] = body.mode
        if body.ref_images is not None:
            safe = []
            for raw in body.ref_images:
                candidate = (UPLOADS / Path(raw).name).resolve()
                if candidate.is_file() and candidate.parent == UPLOADS.resolve():
                    safe.append(str(candidate))
            fields["ref_images"] = safe

        requeued = job["status"] in {"done", "failed", "cancelled"}
        if requeued:
            fields.update(status="queued", attempts=0, error=None,
                          remote_id=None, output_path=None, finished_at=None)
        db.update_job(job_id, **fields)
        return {"ok": True, "requeued": requeued, "job": db.get_job(job_id)}

    @app.get("/api/image/{name}")
    async def get_image(name: str):
        """Serve an uploaded reference image so the editor can preview it.

        Resolved strictly inside the uploads directory - the name comes from the
        browser and must not be able to address anything else on disk.
        """
        target = (UPLOADS / Path(name).name).resolve()
        if not target.is_file() or target.parent != UPLOADS.resolve():
            raise HTTPException(404, "no such image")
        return FileResponse(target)

    @app.post("/api/jobs/{job_id}/retry")
    async def retry(job_id: str) -> dict[str, Any]:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        db.update_job(job_id, status="queued", attempts=0, error=None,
                      remote_id=None, output_path=None)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/again")
    async def run_again(job_id: str) -> dict[str, Any]:
        """Queue a fresh copy of a finished job.

        A clone rather than a reset, so the earlier take and its file stay on record -
        re-rolling a prompt is how this is used, and comparing takes is the point.
        The seed is deliberately not copied: reusing it would reproduce the same clip.
        """
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        new_id = db.add_job(
            job["prompt"],
            seconds=job["seconds"],
            ref_images=job["ref_images"],
            mode=job["mode"],
            preset=job["preset"],
            owner=job.get("owner") or "me",
        )
        return {"ok": True, "job_id": new_id}

    @app.post("/api/jobs/again-all")
    async def run_all_again(body: AgainAllBody) -> dict[str, Any]:
        """Re-queue every job matching a status - the 'that whole batch again' case."""
        wanted = body.status or "done"
        if wanted not in {"done", "failed", "cancelled"}:
            raise HTTPException(400, "status must be done, failed or cancelled")
        created = 0
        for job in db.list_jobs():
            if job["status"] != wanted:
                continue
            db.add_job(
                job["prompt"], seconds=job["seconds"], ref_images=job["ref_images"],
                mode=job["mode"], preset=job["preset"],
                owner=job.get("owner") or "me",
            )
            created += 1
        return {"queued": created}

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str) -> dict[str, Any]:
        """Remove one entry from the list. The rendered file on disk is left alone -
        the queue is a work list, not the archive, and deleting a row should never
        destroy something the user waited and paid for."""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        if job["status"] == "running":
            raise HTTPException(409, "that job is generating right now - cancel it first")
        with db.connect() as con:
            con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str) -> dict[str, Any]:
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        db.update_job(job_id, status="cancelled")
        return {"ok": True}

    @app.post("/api/jobs/clear-finished")
    async def clear_finished() -> dict[str, Any]:
        with db.connect() as con:
            cur = con.execute("DELETE FROM jobs WHERE status IN ('done','cancelled')")
            return {"removed": cur.rowcount}

    @app.post("/api/estimate")
    async def estimate(body: NewJobs) -> dict[str, Any]:
        prompts = [p for p in body.prompts.splitlines() if p.strip()]
        clips = max(1, len(prompts)) * max(1, min(10, body.count))
        preset = cfg.generation.preset(body.preset)
        seconds = body.seconds or cfg.generation.default_seconds
        gpu = cfg.runpod.gpu_preference[0] if cfg.runpod.gpu_preference else ""
        est = estimate_batch(gpu, clips, preset, seconds, cfg)
        return {"clips": clips, **est}

    # ---------- control ----------

    @app.post("/api/policy")
    async def set_policy(body: PolicyBody) -> dict[str, Any]:
        try:
            orch.set_policy(body.policy)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"policy": orch.policy}

    @app.post("/api/folder")
    async def set_folder(body: FolderBody) -> dict[str, Any]:
        folder = Path(body.folder).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"cannot use that folder: {e}")
        cfg.output.folder = str(folder)
        if hasattr(orch.sink, "set_folder"):
            orch.sink.set_folder(folder)
        cfg.save()
        return {"folder": str(folder)}

    @app.post("/api/folder/open")
    async def open_folder() -> dict[str, Any]:
        folder = Path(cfg.output.folder)
        folder.mkdir(parents=True, exist_ok=True)
        _reveal(folder)
        return {"ok": True}

    @app.post("/api/runpod-key")
    async def set_runpod_key(body: KeyBody) -> dict[str, Any]:
        """Save the RunPod key from the UI, so nobody has to edit a YAML file.

        The key is verified against RunPod before it is written. A typo saved
        silently would only surface later as a failed pod start, by which point the
        user has no idea which of several things went wrong.

        It is stored server-side only and never returned - the status endpoint
        exposes just a boolean and the last four characters.
        """
        key = body.key.strip()
        if not key:
            raise HTTPException(400, "no key given")
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get("https://rest.runpod.io/v1/pods",
                                headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            raise HTTPException(502, f"could not reach RunPod to verify the key: {e}")
        if r.status_code == 401:
            raise HTTPException(400, "RunPod rejected that key. Check you copied all of "
                                     "it, and that its permission level is All.")
        if r.status_code >= 400:
            raise HTTPException(400, f"RunPod returned {r.status_code} for that key")

        cfg.runpod.api_key = key
        cfg.save()
        return {
            "ok": True,
            "hint": key[-4:],
            "restart_required": True,
            "note": "Key verified and saved. Quit and reopen H3 Studio to use it - "
                    "the demo badge disappearing is how you will know it took.",
        }

    @app.post("/api/inbox/upload")
    async def upload_to_inbox(file: UploadFile = File(...)) -> dict[str, Any]:
        """Drop a zip or batch file in from the browser.

        Written into the watched folder rather than handled here, so a file dragged
        onto the page and a file saved into the folder by another app go through
        exactly the same unpacking, validation and queueing. Dragging a zip onto the
        window is the obvious gesture; before this it silently did nothing.
        """
        inbox.ensure()
        name = Path(file.filename or "dropped").name
        suffix = Path(name).suffix.lower()
        accepted = ARCHIVE_SUFFIXES | BATCH_SUFFIXES | IMAGE_SUFFIXES
        if suffix not in accepted:
            raise HTTPException(
                400, f"{name}: H3 Studio takes images, .zip archives, or .txt/.json "
                     f"batch files")
        dest = inbox.folder / name
        n = 2
        while dest.exists():
            dest = inbox.folder / f"{Path(name).stem}_{n}{suffix}"
            n += 1
        dest.write_bytes(await file.read())
        return {"ok": True, "name": dest.name,
                "note": "Picked up within a couple of seconds."}

    @app.post("/api/inbox/open")
    async def open_inbox() -> dict[str, Any]:
        inbox.ensure()
        _reveal(inbox.folder)
        return {"ok": True}

    @app.post("/api/quit")
    async def quit_app() -> dict[str, Any]:
        """Stop the whole app from the web page, so the console window is optional.

        Setting should_exit unwinds uvicorn normally, which runs the lifespan's
        finally block - and that is what terminates the rented pod. Killing the
        process instead would leave the GPU running and billing.
        """
        server = getattr(app.state, "server", None)
        if server is None:
            raise HTTPException(
                501, "this instance was not started by the launcher, so it cannot "
                     "stop itself - close its window instead")
        server.should_exit = True
        return {"ok": True, "stopping": True}

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
        UPLOADS.mkdir(parents=True, exist_ok=True)
        suffix = Path(file.filename or "ref.png").suffix or ".png"
        dest = UPLOADS / f"{uuid.uuid4().hex[:10]}{suffix}"
        dest.write_bytes(await file.read())
        return {"path": str(dest), "name": dest.name}

    @app.get("/api/preview/{job_id}")
    async def preview(job_id: str):
        job = db.get_job(job_id)
        if not job or not job.get("output_path"):
            raise HTTPException(404, "no output for that job")
        p = Path(job["output_path"])
        if not p.exists():
            raise HTTPException(404, "file moved or deleted")
        return FileResponse(p, media_type="video/mp4")

    # ---------- static ----------

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    async def index():
        page = WEB / "index.html"
        if not page.exists():
            return JSONResponse({"error": "web/index.html missing"}, status_code=500)
        html = page.read_text(encoding="utf-8")
        # Stamp the asset URLs with a fingerprint of their contents. Without it the
        # browser happily keeps a cached app.js after an update, so the page renders
        # new markup while running old code - a failure that looks like a bug in the
        # feature you just changed rather than a caching problem.
        for asset in ("app.js", "style.css"):
            path = WEB / asset
            if path.exists():
                stamp = f"{int(path.stat().st_mtime)}-{path.stat().st_size}"
                html = html.replace(f"/static/{asset}", f"/static/{asset}?v={stamp}")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    return app


def main() -> None:
    ap = argparse.ArgumentParser(prog="h3studio")
    ap.add_argument("--mock", action="store_true",
                    help="fake backend: exercises the full pipeline without renting a GPU")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = config_mod.load(Path(args.config) if args.config else None)
    cfg.mock = args.mock
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port

    if _port_busy(host, port):
        # Errno 10048 on its own tells you nothing. Almost always this is another
        # copy of H3 Studio still running - which also still owns a GPU pod if it
        # is not the mock, so say that rather than just failing to bind.
        print(f"\n  Port {port} is already in use.\n")
        print("  Most likely another copy of H3 Studio is still running.")
        print(f"  Open http://{host}:{port} - if that is your app, use it, or stop it")
        print("  with Ctrl-C in its window.\n")
        print("  To stop whatever holds the port, in PowerShell:")
        print(f"    Get-NetTCPConnection -LocalPort {port} -State Listen | "
              "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }\n")
        print(f"  Or just use another port:  python -m app.main --port {port + 1}\n")
        raise SystemExit(1)

    db.init()
    app = create_app(cfg)
    banner = "MOCK (no GPU, no cost)" if cfg.mock else f"RunPod {cfg.runpod.gpu_preference[0]}"
    print(f"\n  H3 Studio  ->  http://{host}:{port}   [{banner}]\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def _port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # No SO_REUSEADDR: on Windows that would let the bind succeed against a
        # listener that is still there, hiding the very conflict we are testing for.
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


if __name__ == "__main__":
    main()
