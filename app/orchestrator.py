"""The single background loop that owns the pod and drains the queue.

Why the ON/OFF button is a *policy* and not a raw switch:

  auto        pod comes up when there is work, dies `idle_shutdown_minutes` after the
              queue empties. This is also the thing that stops a pod being left
              running overnight, which is the only way this project can cost real money.
  keep-warm   stays up regardless, for a session of rapid iteration where a 5 minute
              boot between takes is worse than a few cents of idle.
  off         drain nothing, terminate anything running.

With one user that is a convenience. With five it is a necessity - five people cannot
each hold their own on/off switch over one shared pod. Same code either way, which is
the whole reason it is written like this now.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from . import db
from .backends import Backend, PodStatus
from .config import Config
from .sinks import OutputSink, make_sink

log = logging.getLogger("h3studio.orchestrator")

MAX_ATTEMPTS = 3          # a job that fails 3 times is a bad job, not bad luck
MAX_INFLIGHT = 2          # keep ComfyUI's own queue fed so the GPU never idles
TICK_SECONDS = 3.0

# A poll that keeps throwing must eventually fail its job. Without this a single
# persistently broken job sits in 'running' forever, holding an inflight slot and
# keeping the pod alive - which is the expensive version of a hung queue.
MAX_POLL_ERRORS = 20


class Orchestrator:
    def __init__(self, cfg: Config, backend: Backend) -> None:
        self.cfg = cfg
        self.backend = backend
        self.sink: OutputSink = make_sink(cfg)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._inflight: dict[str, str] = {}      # job_id -> remote_id
        self._poll_errors: dict[str, int] = {}   # consecutive poll failures per job
        self._pod_status = PodStatus()
        self._session_started: float | None = None
        self._last_busy: float = time.time()
        self._run_id: str | None = None
        self._last_error: str = ""
        self._notice: str = ""

    # ---------- policy ----------

    @property
    def policy(self) -> str:
        return db.kv_get("pod_policy", self.cfg.pod.policy) or "auto"

    def set_policy(self, value: str) -> None:
        if value not in {"auto", "keep-warm", "off"}:
            raise ValueError(f"bad policy {value!r}")
        db.kv_set("pod_policy", value)
        self._notice = f"policy set to {value}"

    # ---------- lifecycle ----------

    async def start(self) -> None:
        db.init()
        orphaned = db.requeue_stuck_running()
        if orphaned:
            log.info("requeued %d job(s) orphaned by a previous run", orphaned)
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="orchestrator")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.wait([self._task], timeout=20)
        # A pod outliving the process is the expensive failure mode. Always try.
        try:
            await self.backend.shutdown()
        except Exception:
            log.exception("shutdown during stop failed")
        await self.backend.aclose()

    # ---------- status for the UI ----------

    def snapshot(self) -> dict[str, Any]:
        cost = getattr(self.backend, "cost_so_far", lambda: 0.0)()
        session_s = time.time() - self._session_started if self._session_started else 0.0
        return {
            "policy": self.policy,
            "pod": {
                "state": self._pod_status.state,
                "detail": self._pod_status.detail,
                "pod_id": self._pod_status.pod_id,
                "uptime_s": round(self._pod_status.uptime_s, 1),
                "gpu": getattr(self.backend, "gpu_used", "") or "",
                "rate_per_hour": getattr(self.backend, "rate_per_hour", 0.0),
            },
            "counts": db.counts(),
            "inflight": len(self._inflight),
            "session": {
                "seconds": round(session_s, 1),
                "cost_usd": round(cost, 4),
                "limit_usd": self.cfg.budget.session_limit_usd,
                "warn_usd": self.cfg.budget.warn_at_usd,
            },
            "backend": self.backend.name,
            "output_folder": str(getattr(self.sink, "folder", "")),
            "error": self._last_error,
            "notice": self._notice,
        }

    # ---------- the loop ----------

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as e:
                self._last_error = str(e)[:400]
                log.exception("orchestrator tick failed")
                # Never spin hot on a persistent failure.
                await asyncio.sleep(5)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        counts = db.counts()
        want_work = counts["queued"] > 0 or self._inflight
        policy = self.policy

        if policy == "off":
            if self._pod_status.state not in {"off", "stopping"}:
                await self._teardown("policy off")
            self._pod_status = await self.backend.status()
            return

        if not want_work and policy == "auto":
            self._pod_status = await self.backend.status()
            if self._pod_status.state == "ready":
                idle_for = time.time() - self._last_busy
                if idle_for >= self.cfg.pod.idle_shutdown_minutes * 60:
                    await self._teardown(
                        f"idle {self.cfg.pod.idle_shutdown_minutes} min"
                    )
            return

        if want_work or policy == "keep-warm":
            if not await self._ensure_pod():
                return

        if await self._enforce_ceilings():
            return

        await self._collect()
        await self._dispatch()

        if self._inflight or db.counts()["queued"]:
            self._last_busy = time.time()

    async def _ensure_pod(self) -> bool:
        self._pod_status = await self.backend.status()
        if self._pod_status.state == "ready":
            return True
        if self._pod_status.state in {"off", "error"}:
            self._notice = ("starting GPU - first boot downloads "
                            f"~{self.cfg.weights.total_gb_hint():.0f}GB of weights")
            self._session_started = time.time()
            self._run_id = db.start_run(None, getattr(self.backend, "gpu_used", "") or "?",
                                        note="auto start")
        try:
            self._pod_status = await self.backend.ensure_ready()
        except Exception as e:
            self._last_error = f"pod start failed: {str(e)[:300]}"
            if self._run_id:
                db.update_run(self._run_id, status="error", ended_at=time.time(),
                              note=self._last_error)
                self._run_id = None
            return False
        if self._run_id:
            db.update_run(self._run_id, status="ready", pod_id=self._pod_status.pod_id,
                          endpoint=self._pod_status.endpoint,
                          gpu_type=getattr(self.backend, "gpu_used", "") or "?")
        self._last_error = ""
        self._notice = ""
        return True

    async def _enforce_ceilings(self) -> bool:
        """Hard stops that exist so a bug cannot turn into a bill. Returns True if stopped."""
        cost = getattr(self.backend, "cost_so_far", lambda: 0.0)()
        if cost >= self.cfg.budget.session_limit_usd:
            await self._teardown(f"budget ceiling ${self.cfg.budget.session_limit_usd:.2f} hit")
            self.set_policy("off")
            return True
        if self._session_started:
            hours = (time.time() - self._session_started) / 3600.0
            if hours >= self.cfg.pod.max_session_hours:
                await self._teardown(f"max session {self.cfg.pod.max_session_hours}h hit")
                self.set_policy("off")
                return True
        return False

    async def _dispatch(self) -> None:
        while len(self._inflight) < MAX_INFLIGHT:
            job = db.claim_next_queued()
            if job is None:
                return
            try:
                for name in job.get("ref_images") or []:
                    p = Path(name)
                    if p.exists():
                        await self.backend.upload_image(p)
                remote_id = await self.backend.submit(job)
            except Exception as e:
                await self._fail_or_retry(job, str(e)[:400])
                continue
            self._inflight[job["id"]] = remote_id
            db.update_job(job["id"], remote_id=remote_id)
            self._last_busy = time.time()

    async def _collect(self) -> None:
        for job_id, remote_id in list(self._inflight.items()):
            try:
                res = await self.backend.poll(remote_id)
            except Exception as e:
                n = self._poll_errors.get(job_id, 0) + 1
                self._poll_errors[job_id] = n
                self._last_error = f"poll failed ({n}/{MAX_POLL_ERRORS}): {str(e)[:200]}"
                if n >= MAX_POLL_ERRORS:
                    self._inflight.pop(job_id, None)
                    self._poll_errors.pop(job_id, None)
                    job = db.get_job(job_id)
                    if job:
                        await self._fail_or_retry(job, f"polling kept failing: {str(e)[:300]}")
                continue
            self._poll_errors.pop(job_id, None)
            if res.state in {"pending", "running"}:
                continue
            self._inflight.pop(job_id, None)
            job = db.get_job(job_id)
            if job is None:
                continue
            if res.state == "failed":
                await self._fail_or_retry(job, res.error or "unknown failure")
                continue
            try:
                path = await self.sink.put(job, res.video or b"", res.filename or "clip.mp4")
            except Exception as e:
                await self._fail_or_retry(job, f"saving output failed: {str(e)[:300]}")
                continue
            db.update_job(job_id, status="done", finished_at=time.time(),
                          output_path=path, error=None)
            self._last_busy = time.time()

    async def _fail_or_retry(self, job: dict[str, Any], error: str) -> None:
        if job.get("attempts", 0) < MAX_ATTEMPTS:
            db.update_job(job["id"], status="queued", error=error, remote_id=None)
        else:
            db.update_job(job["id"], status="failed", error=error,
                          finished_at=time.time(), remote_id=None)

    async def _teardown(self, reason: str) -> None:
        log.info("stopping pod: %s", reason)
        self._notice = f"GPU stopped ({reason})"
        cost = getattr(self.backend, "cost_so_far", lambda: 0.0)()
        try:
            await self.backend.shutdown()
        finally:
            if self._run_id:
                db.update_run(self._run_id, status="stopped", ended_at=time.time(),
                              cost_estimate=round(cost, 4), note=reason)
                self._run_id = None
            self._session_started = None
            self._inflight.clear()
            self._poll_errors.clear()
            self._pod_status = PodStatus(state="off", detail=reason)
