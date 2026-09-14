"""The single background loop that owns the pod and drains the queue.

Why the ON/OFF button is a *policy* and not a raw switch:

  auto        pod comes up when there is work, dies `idle_shutdown_minutes` after
              the queue empties. This is also the thing that stops a pod being
              left running overnight, which is the only way this project costs
              real money.
  keep-warm   stays up regardless, for a session of rapid iteration where a five
              minute boot between takes is worse than a few cents of idle.
  off         drain nothing, terminate anything running.

With one user that was a convenience. With several it is a necessity - people
cannot each hold their own on/off switch over one shared pod, so the policy is
admin-owned and the queue is global.

Exactly one of these may run. It owns a rented GPU, and a second copy would mean
a second pod billing at once with nothing erroring, so leadership is decided by a
Postgres advisory lock rather than by hoping the replica count stays at one.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import PurePosixPath
from typing import Any

from .backends import Backend, PodStatus
from .config import Config
from .store import ORCHESTRATOR_LOCK_KEY, get_pool, try_advisory_lock
from .sinks import fit_reference
from .store import jobs, kv, runs

log = logging.getLogger("h3studio.orchestrator")

MAX_ATTEMPTS = 3          # a job that fails 3 times is a bad job, not bad luck
MAX_INFLIGHT = 2          # keep ComfyUI's own queue fed so the GPU never idles
TICK_SECONDS = 3.0

# A poll that keeps throwing must eventually fail its job. Without this a single
# persistently broken job sits in 'running' forever, holding an inflight slot and
# keeping the pod alive - which is the expensive version of a hung queue.
MAX_POLL_ERRORS = 20
# Consecutive polls the pod may answer "never heard of it" before the job goes
# back to the queue. One is given the benefit of the doubt; two is a lost render.
LOST_POLLS = 2

POLICIES = {"auto", "keep-warm", "off"}

# How often a follower asks for the queue lock again. Dokploy rolls out a new
# container before stopping the old one, so every deploy starts as a follower
# and must take over the moment the old process lets go.
LEADER_RETRY_SECONDS = 5.0

USER_POD_DETAIL = {
    "off": "The GPU starts when there is something to render.",
    "booting": "Starting up. The first clip of a session takes a few minutes.",
    "ready": "Ready.",
    "stopping": "Shutting down.",
    "error": "The GPU could not start. An admin has been told.",
}


# A pod start that just failed fails the same way three seconds later. Without a
# pause the loop retries it twenty times a minute, each attempt another POST at
# RunPod and another error row in `runs`. Ported from Oren Suchard's
# queue-reorder-and-pod-recovery, whose orchestrator half predates this file.
POD_RETRY_SECONDS = 60.0


class Orchestrator:
    def __init__(self, cfg: Config, backend: Backend, sink: Any,
                 storage: Any) -> None:
        self.cfg = cfg
        self.backend = backend
        self.sink = sink
        self.storage = storage
        self.leader = False
        self._run_loop = True
        self._lock_conn = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._inflight: dict[str, str] = {}      # job_id -> remote_id
        self._poll_errors: dict[str, int] = {}   # consecutive poll failures per job
        self._lost_polls: dict[str, int] = {}    # consecutive "no such render" per job
        self._pod_status = PodStatus()
        self._session_started: float | None = None
        self._last_busy: float = time.time()
        self._run_id: str | None = None
        self._pod_retry_at: float = 0.0
        self._last_error: str = ""
        self._notice: str = ""          # admin-facing, may name prices and reasons
        self._user_notice: str = ""     # what every user sees; never money or policy

    # ---------- policy ----------

    async def policy(self) -> str:
        return await kv.get("pod_policy", self.cfg.pod.policy) or "off"

    async def sage_enabled(self) -> bool:
        """The experimental faster attention: the Admin switch, else the config default."""
        value = await kv.get("sage_attention", None)
        if value is None:
            return bool(self.cfg.generation.sage_attention)
        return value == "on"

    async def set_policy(self, value: str, *, announce: bool = True) -> None:
        if value not in POLICIES:
            raise ValueError(f"bad policy {value!r}")
        await kv.set("pod_policy", value)
        if announce:
            self._notice = f"policy set to {value}"

    # ---------- lifecycle ----------

    async def start(self, *, run_loop: bool = True) -> None:
        """Claim leadership, or wait for it, then drain the queue.

        The lock is held on a dedicated connection for the life of the process.
        Two orchestrators would mean two rented pods billing at once, so a
        process without the lock touches no GPU. But it keeps asking: a rolling
        deploy starts the new container while the old one still holds the lock,
        and a follower that gave up on the first try would leave nobody draining
        the queue once the old container exited.

        run_loop=False leaves ticking to the caller. The tests drive _tick() by
        hand, and a background loop ticking at the same time makes their state
        assertions a race against the scheduler.
        """
        self._run_loop = run_loop
        self._lock_conn = await get_pool().getconn()
        # Autocommit, or this connection sits "idle in transaction" for the life
        # of the process - which pins the transaction horizon and stops VACUUM
        # cleaning up dead rows across the whole database. A session-level
        # advisory lock is held by the connection, not by a transaction, so
        # nothing about the locking depends on leaving one open.
        await self._lock_conn.set_autocommit(True)
        self._stop.clear()
        if await try_advisory_lock(self._lock_conn, ORCHESTRATOR_LOCK_KEY):
            await self._become_leader()
            if run_loop:
                self._task = asyncio.create_task(self._loop(), name="orchestrator")
        else:
            log.warning("another orchestrator holds the queue lock; waiting to take "
                        "over - this process will not start or stop any GPU until then")
            self._task = asyncio.create_task(self._await_leadership(),
                                             name="orchestrator-standby")

    async def _become_leader(self) -> None:
        self.leader = True
        # Whatever the previous leader was rendering died with it.
        orphaned = await jobs.requeue_stuck_running()
        if orphaned:
            log.info("requeued %d job(s) orphaned by a previous run", orphaned)

    async def _await_leadership(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=LEADER_RETRY_SECONDS)
                return
            except asyncio.TimeoutError:
                pass
            try:
                got = await try_advisory_lock(self._lock_conn, ORCHESTRATOR_LOCK_KEY)
            except Exception:
                log.exception("asking for the queue lock failed")
                continue
            if got:
                log.info("took over the queue lock")
                await self._become_leader()
                if self._run_loop:
                    await self._loop()
                return

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.wait([self._task], timeout=20)
            self._task = None
        if self.leader:
            # A pod outliving the process is the expensive failure mode. The
            # full teardown, not a bare shutdown: that also closes the session
            # row with its cost and puts what was rendering back in the queue.
            # Every deploy used to leave a row open at $0, which is why the
            # cost history under-reported and the runs table filled with
            # sessions that never ended.
            try:
                await self._teardown("process stopping")
            except Exception:
                log.exception("shutdown during stop failed")
        await self.backend.aclose()
        if self._lock_conn is not None:
            # Release explicitly. A session-level lock survives the connection
            # going back to the pool, so without this the lock would stay held
            # by an idle pooled connection and no other process could lead.
            try:
                await self._lock_conn.execute("SELECT pg_advisory_unlock_all()")
            except Exception:
                log.exception("could not release the queue lock")
            await get_pool().putconn(self._lock_conn)
            self._lock_conn = None
        self.leader = False

    # ---------- status for the UI ----------

    async def snapshot(self) -> dict[str, Any]:
        """The shared, admin-facing view."""
        cost = getattr(self.backend, "cost_so_far", lambda: 0.0)()
        session_s = time.time() - self._session_started if self._session_started else 0.0
        return {
            "leader": self.leader,
            "policy": await self.policy(),
            "sage": await self.sage_enabled(),
            "pod": {
                "state": self._pod_status.state,
                "detail": self._pod_status.detail,
                "pod_id": self._pod_status.pod_id,
                "uptime_s": round(self._pod_status.uptime_s, 1),
                "gpu": getattr(self.backend, "gpu_used", "") or "",
                "rate_per_hour": getattr(self.backend, "rate_per_hour", 0.0),
            },
            "counts": await jobs.counts_all(),
            "inflight": len(self._inflight),
            "session": {
                "seconds": round(session_s, 1),
                "cost_usd": round(cost, 4),
                "limit_usd": self.cfg.budget.session_limit_usd,
                "warn_usd": self.cfg.budget.warn_at_usd,
            },
            "backend": self.backend.name,
            "error": self._last_error,
            "notice": self._notice,
        }

    async def snapshot_for(self, user_id: str) -> dict[str, Any]:
        """What one user is allowed to see.

        Pod state and a queue depth, because a job waiting through a five-minute
        cold boot is otherwise indistinguishable from a broken one. No prompts, no
        costs, no other user's counts.
        """
        counts_all = await jobs.counts_all()
        state = self._pod_status.state
        return {
            "pod": {
                "state": state,
                # The raw detail carries the GPU price, pod ids and exception
                # text; users get one fixed phrase per state instead.
                "detail": USER_POD_DETAIL.get(state, ""),
                "uptime_s": round(self._pod_status.uptime_s, 1),
            },
            "counts": await jobs.counts_for(user_id),
            "queue": {
                "total_queued": counts_all["queued"],
                "total_running": counts_all["running"],
            },
            "backend": self.backend.name,
            "notice": self._user_notice,
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
        counts = await jobs.counts_all()
        want_work = counts["queued"] > 0 or self._inflight
        policy = await self.policy()

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
                        f"idle {self.cfg.pod.idle_shutdown_minutes} min")
            return

        if want_work or policy == "keep-warm":
            if not await self._ensure_pod():
                return

        if await self._enforce_ceilings():
            return

        await self._collect()
        await self._dispatch()

        if self._inflight or (await jobs.counts_all())["queued"]:
            self._last_busy = time.time()

    async def _open_run(self, note: str) -> None:
        """Record the GPU session, once, however the pod came to be running.

        This used to happen only when the pod was first seen `off`, which missed
        every pod this process did not start itself: a start whose first
        connections timed out leaves a pod booting, the next tick adopts it, and
        the whole session then rendered without ever appearing in the cost
        history. The budget ceiling reads the backend's own clock and was never
        affected, but `runs` is what an admin actually looks at.
        """
        if self._run_id is not None:
            return
        self._session_started = self._session_started or time.time()
        self._run_id = await runs.start(
            self._pod_status.pod_id, getattr(self.backend, "gpu_used", "") or "?",
            note=note)

    async def _mark_run_ready(self) -> None:
        if not self._run_id:
            return
        await runs.update(self._run_id, status="ready",
                          pod_id=self._pod_status.pod_id,
                          endpoint=self._pod_status.endpoint,
                          gpu_type=getattr(self.backend, "gpu_used", "") or "?")

    async def _ensure_pod(self) -> bool:
        self._pod_status = await self.backend.status()
        if self._pod_status.state == "ready":
            # Already up, possibly from an earlier attempt or another process.
            if self._run_id is None:
                await self._open_run("adopted a running pod")
                await self._mark_run_ready()
            self._pod_came_up()
            return True
        if time.time() < self._pod_retry_at:
            return False
        if self._run_id is None:
            self._notice = ("starting GPU - first boot downloads "
                            f"~{self.cfg.weights.total_gb_hint():.0f}GB of weights")
            self._user_notice = ("Starting the GPU. The first clip of a session "
                                 "takes a few minutes to begin.")
            await self._open_run("auto start"
                                 if self._pod_status.state in {"off", "error"}
                                 else f"adopted a pod that was {self._pod_status.state}")
        # The header reads `_pod_status`; from here until the pod answers it must
        # say "booting", not the "off" from before the start.
        self._pod_status = PodStatus(state="booting", detail="starting the GPU")

        def publish(st: PodStatus) -> None:
            self._pod_status = st

        try:
            self._pod_status = await self.backend.ensure_ready(on_status=publish)
        except Exception as e:
            self._last_error = f"pod start failed: {str(e)[:300]}"
            self._pod_retry_at = time.time() + POD_RETRY_SECONDS
            if self._run_id:
                await runs.update(self._run_id, status="error", ended_at=time.time(),
                                  note=self._last_error)
                self._run_id = None
            return False
        await self._mark_run_ready()
        self._pod_came_up()
        return True

    STARTING_ERROR = "pod start failed"
    STARTING_NOTICE = "starting GPU"
    STARTING_USER_NOTICE = "Starting the GPU"

    def _pod_came_up(self) -> None:
        """Whatever was said while the pod started is no longer true.

        A start attempt that failed on one status poll left "pod start failed"
        on the admin page and "Starting the GPU" on everyone's feed for the
        whole session, because the pod then came up through the adopted-pod
        path above, which never cleared them. Only the starting messages are
        cleared here: an error from a render must stay visible.
        """
        self._pod_retry_at = 0.0
        if self._last_error.startswith(self.STARTING_ERROR):
            self._last_error = ""
        if self._notice.startswith(self.STARTING_NOTICE):
            self._notice = ""
        if self._user_notice.startswith(self.STARTING_USER_NOTICE):
            self._user_notice = ""

    async def _enforce_ceilings(self) -> bool:
        """Hard stops that exist so a bug cannot turn into a bill.

        Returns True if it stopped everything.
        """
        cost = getattr(self.backend, "cost_so_far", lambda: 0.0)()
        if cost >= self.cfg.budget.session_limit_usd:
            await self._teardown(
                f"budget ceiling ${self.cfg.budget.session_limit_usd:.2f} hit")
            await self.set_policy("off", announce=False)
            return True
        if self._session_started:
            hours = (time.time() - self._session_started) / 3600.0
            if hours >= self.cfg.pod.max_session_hours:
                await self._teardown(
                    f"max session {self.cfg.pod.max_session_hours}h hit")
                await self.set_policy("off", announce=False)
                return True
        return False

    async def _dispatch(self) -> None:
        while len(self._inflight) < MAX_INFLIGHT:
            job = await jobs.claim_next_queued()
            if job is None:
                return
            stage = "preparing"
            try:
                preset = self.cfg.generation.preset(job.get("preset"))
                names: list[str] = []
                for key in job.get("ref_images") or []:
                    name = PurePosixPath(key).name
                    stage = f"reading {name}"
                    data = await self.storage.get(key)
                    data, name = await asyncio.to_thread(
                        fit_reference, data, name, preset.width, preset.height)
                    stage = f"sending {name} to the GPU"
                    names.append(await self.backend.upload_image(data, name))
                keyframes = []
                for kf in job.get("keyframes") or []:
                    name = PurePosixPath(str(kf.get("key", ""))).name
                    stage = f"reading {name}"
                    data = await self.storage.get(kf["key"])
                    data, name = await asyncio.to_thread(
                        fit_reference, data, name, preset.width, preset.height)
                    stage = f"sending {name} to the GPU"
                    keyframes.append({**kf, "name": await self.backend.upload_image(data, name)})
                ref_video_names, ref_audio_names = [], []
                for key in job.get("ref_videos") or []:
                    name = PurePosixPath(str(key)).name
                    stage = f"reading {name}"
                    data = await self.storage.get(key)
                    stage = f"sending {name} to the GPU"
                    ref_video_names.append(await self.backend.upload_image(data, name))
                for key in job.get("ref_audios") or []:
                    name = PurePosixPath(str(key)).name
                    stage = f"reading {name}"
                    data = await self.storage.get(key)
                    stage = f"sending {name} to the GPU"
                    ref_audio_names.append(await self.backend.upload_image(data, name))
                audio_name = None
                if job.get("audio_key"):
                    name = PurePosixPath(str(job["audio_key"])).name
                    stage = f"reading {name}"
                    data = await self.storage.get(job["audio_key"])
                    stage = f"sending {name} to the GPU"
                    # Same door as the images: /upload/image writes any bytes.
                    audio_name = await self.backend.upload_image(data, name)
                stage = "submitting to the GPU"
                # The names ComfyUI stored, which is what the graph must reference.
                remote_id = await self.backend.submit(
                    {**job, "ref_images": names, "keyframes": keyframes,
                     "audio_name": audio_name, "ref_video_names": ref_video_names,
                     "ref_audio_names": ref_audio_names,
                     "sage": await self.sage_enabled()})
            except Exception as e:
                # httpx timeouts stringify to nothing; a blank error in the feed is
                # what "it failed again" looked like.
                reason = str(e) or type(e).__name__
                await self._fail_or_retry(job, f"{stage} failed: {reason}"[:400])
                continue
            self._inflight[job["id"]] = remote_id
            await jobs.update(job["id"], remote_id=remote_id)
            self._last_busy = time.time()

    async def _collect(self) -> None:
        for job_id, remote_id in list(self._inflight.items()):
            job = await jobs.get_any(job_id)
            # Cancelled while it rendered. Until this check the GPU finished the
            # clip anyway, at full price, with one of the two slots held by a
            # job nobody wanted - which is what "the queue is stuck" was.
            if job is None or job["status"] != "running":
                await self._stop_render(job_id, remote_id)
                continue
            try:
                res = await self.backend.poll(remote_id)
            except Exception as e:
                n = self._poll_errors.get(job_id, 0) + 1
                self._poll_errors[job_id] = n
                self._last_error = f"poll failed ({n}/{MAX_POLL_ERRORS}): {str(e)[:200]}"
                if n >= MAX_POLL_ERRORS:
                    self._forget(job_id)
                    await self._fail_or_retry(
                        job, f"polling kept failing: {str(e)[:300]}")
                continue
            self._poll_errors.pop(job_id, None)
            if res.state == "lost":
                n = self._lost_polls.get(job_id, 0) + 1
                self._lost_polls[job_id] = n
                if n < LOST_POLLS:
                    continue
                self._forget(job_id)
                await self._fail_or_retry(
                    job, "the GPU has no record of this render - it was probably replaced")
                continue
            self._lost_polls.pop(job_id, None)
            if res.state in {"pending", "running"}:
                continue
            self._forget(job_id)
            # Read again: the row is checked as late as possible, so a cancel that
            # landed during the poll still wins over what the GPU produced.
            job = await jobs.get_any(job_id)
            # Cancelled (or edited) while it rendered: the user's decision wins
            # over whatever the GPU produced.
            if job is None or job["status"] != "running":
                continue
            if res.state == "failed":
                await self._fail_or_retry(job, res.error or "unknown failure")
                continue
            video = res.video or b""
            try:
                key = await self.sink.put(job, video, res.filename or "clip.mp4")
            except Exception as e:
                await self._fail_or_retry(job, f"saving output failed: {str(e)[:300]}")
                continue
            poster = None
            make_poster = getattr(self.sink, "poster", None)
            if make_poster is not None:
                try:
                    poster = await make_poster(job, video)
                except Exception:
                    log.warning("no poster for %s", job_id, exc_info=True)
            finished = await jobs.update_if(
                job_id, "running", status="done", finished_at=time.time(),
                output_key=key, output_bytes=len(video), poster_key=poster,
                error=None)
            if not finished:
                # Cancelled between the upload and this line: don't leave the
                # files behind with nothing pointing at them.
                for stale in (key, poster):
                    if not stale:
                        continue
                    try:
                        await self.storage.delete(stale)
                    except Exception:
                        log.warning("could not remove %s after a late cancel", stale)
            self._last_busy = time.time()

    def _forget(self, job_id: str) -> None:
        self._inflight.pop(job_id, None)
        self._poll_errors.pop(job_id, None)
        self._lost_polls.pop(job_id, None)

    async def _stop_render(self, job_id: str, remote_id: str) -> None:
        """The user no longer wants it: free the slot and tell the GPU."""
        self._forget(job_id)
        try:
            await self.backend.cancel(remote_id)
        except Exception as e:
            # The slot is free either way. What is lost is the money for the
            # rest of this render, which is worth an admin's attention.
            log.warning("cancel of %s did not reach the GPU: %s", job_id, e)
            self._last_error = f"cancel did not reach the GPU: {str(e)[:200]}"

    async def _fail_or_retry(self, job: dict[str, Any], error: str) -> None:
        # Guarded on 'running' so a job cancelled mid-render is not resurrected
        # by its own failure.
        if job.get("attempts", 0) < MAX_ATTEMPTS:
            await jobs.update_if(job["id"], "running", status="queued", error=error,
                                 remote_id=None)
        else:
            await jobs.update_if(job["id"], "running", status="failed", error=error,
                                 finished_at=time.time(), remote_id=None)

    async def _teardown(self, reason: str) -> None:
        log.info("stopping pod: %s", reason)
        self._notice = f"GPU stopped ({reason})"
        self._user_notice = "The GPU was stopped. Queued clips wait until it starts again."
        # Anything mid-render dies with the pod. Put it back in the queue rather
        # than leaving it 'running' until the process restarts - a running job
        # cannot be edited or removed.
        for job_id in list(self._inflight):
            await jobs.update_if(job_id, "running", status="queued", remote_id=None)
        cost = getattr(self.backend, "cost_so_far", lambda: 0.0)()
        try:
            await self.backend.shutdown()
        finally:
            if self._run_id:
                await runs.update(self._run_id, status="stopped",
                                  ended_at=time.time(),
                                  cost_estimate=round(cost, 4), note=reason)
                self._run_id = None
            self._session_started = None
            self._inflight.clear()
            self._poll_errors.clear()
            self._lost_polls.clear()
            self._pod_status = PodStatus(state="off", detail=reason)
