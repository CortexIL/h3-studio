"""The single background loop that owns the pods and drains the queue.

Why the ON/OFF button is a *policy* and not a raw switch:

  auto        pods come up when there is work, each dies `idle_shutdown_minutes`
              after it has nothing left to do. This is also the thing that stops
              a pod being left running overnight, which is the only way this
              project costs real money.
  keep-warm   one pod stays up regardless, for a session of rapid iteration
              where a five minute boot between takes is worse than a few cents
              of idle.
  off         drain nothing, terminate anything running.

With one user that was a convenience. With several it is a necessity - people
cannot each hold their own on/off switch over shared pods, so the policy is
admin-owned and the queue is global.

How many pods: up to the admin's `max_pods` (1-5, default 1). A second pod is
only worth its boot - a weight download of a quarter of an hour, paid at the GPU
rate - when the clips waiting would take longer than that on the pods already
up, so another one is added only while that is true, one per tick. Every pod
closes on its own once it has sat idle. The money ceiling covers all of them
together: it is the session that is limited, not a card.

Exactly one of these may run. It owns rented GPUs, and a second copy would mean
a second set of pods billing at once with nothing erroring, so leadership is
decided by a Postgres advisory lock rather than by hoping the replica count
stays at one.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import PurePosixPath
from typing import Any, Callable

from . import estimate
from .backends import Backend, PodStatus
from .config import Config
from .store import ORCHESTRATOR_LOCK_KEY, get_pool, try_advisory_lock
from .sinks import fit_reference
from .store import jobs, kv, runs

log = logging.getLogger("h3studio.orchestrator")

MAX_ATTEMPTS = 3          # a job that fails 3 times is a bad job, not bad luck
MAX_INFLIGHT = 2          # keep ComfyUI's own queue fed so the GPU never idles
TICK_SECONDS = 3.0

# The most pods the admin may ask for at once. Each is a separate bill, and the
# session ceiling is shared, so five already burns through $8 in under two hours.
MAX_PODS = 5

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

# Which pod speaks for all of them in the header: any that is rendering beats
# one on its way up, which beats one that failed.
_STATE_RANK = {"ready": 4, "booting": 3, "stopping": 2, "error": 1, "off": 0}


# A pod start that just failed fails the same way three seconds later. Without a
# pause the loop retries it twenty times a minute, each attempt another POST at
# RunPod and another error row in `runs`. Ported from Oren Suchard's
# queue-reorder-and-pod-recovery, whose orchestrator half predates this file.
POD_RETRY_SECONDS = 60.0


class PodSlot:
    """One rented GPU and what the loop keeps track of about it.

    A slot outlives its pod: the first one holds the backend the app was built
    with and stays in the list while off, so a single-pod studio behaves exactly
    as it did before there could be more. Extra slots are dropped once closed.
    """

    def __init__(self, backend: Backend, number: int, *, primary: bool = False) -> None:
        self.backend = backend
        self.number = number            # 1-based, what the admin page calls it
        self.primary = primary
        self.status = PodStatus()
        self.run_id: str | None = None
        self.retry_at: float = 0.0
        self.last_busy: float = time.time()
        # A pod starting in the background while others keep rendering.
        self.boot: asyncio.Task | None = None

    @property
    def ready(self) -> bool:
        return self.boot is None and self.status.state == "ready"

    @property
    def active(self) -> bool:
        """Anything here that is, or might be, billing."""
        return (self.boot is not None or self.run_id is not None
                or self.status.state in {"booting", "ready", "error"})

    def cost(self) -> float:
        return getattr(self.backend, "cost_so_far", lambda: 0.0)()

    @property
    def gpu(self) -> str:
        return getattr(self.backend, "gpu_used", "") or ""

    @property
    def rate(self) -> float:
        return getattr(self.backend, "rate_per_hour", 0.0)


class Orchestrator:
    def __init__(self, cfg: Config, backend: Backend, sink: Any,
                 storage: Any,
                 backend_factory: Callable[[], Backend] | None = None) -> None:
        self.cfg = cfg
        self.sink = sink
        self.storage = storage
        # Without a factory there is only ever the one backend, whatever the
        # admin's setting says.
        self._make_backend = backend_factory
        self.slots: list[PodSlot] = [PodSlot(backend, 1, primary=True)]
        self.leader = False
        self._run_loop = True
        self._lock_conn = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._inflight: dict[str, str] = {}        # job_id -> remote_id
        self._placed: dict[str, PodSlot] = {}      # job_id -> the pod rendering it
        self._poll_errors: dict[str, int] = {}     # consecutive poll failures per job
        self._lost_polls: dict[str, int] = {}      # consecutive "no such render" per job
        self._session_started: float | None = None
        # What pods already closed this session cost; the ceiling counts them too.
        self._session_spent: float = 0.0
        self._strays_checked = False
        self._last_error: str = ""
        self._notice: str = ""          # admin-facing, may name prices and reasons
        self._user_notice: str = ""     # what every user sees; never money or policy

    # ---------- the first pod, under the names a single-pod studio used ----------

    @property
    def backend(self) -> Backend:
        return self.slots[0].backend

    @property
    def _pod_status(self) -> PodStatus:
        return self._headline().status

    @property
    def _pod_retry_at(self) -> float:
        return self.slots[0].retry_at

    @_pod_retry_at.setter
    def _pod_retry_at(self, value: float) -> None:
        self.slots[0].retry_at = value

    def _headline(self) -> PodSlot:
        return max(self.slots, key=lambda s: (_STATE_RANK.get(s.status.state, 0),
                                              -s.number))

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

    @property
    def max_pods_allowed(self) -> int:
        return MAX_PODS if self._make_backend is not None else 1

    async def max_pods(self) -> int:
        """How many pods may run at once: the Admin setting, else the config default."""
        raw = await kv.get("max_pods", None)
        try:
            wanted = int(raw) if raw is not None else int(self.cfg.pod.max_pods)
        except ValueError:
            wanted = 1
        return max(1, min(self.max_pods_allowed, wanted))

    async def set_max_pods(self, value: int) -> None:
        if not 1 <= value <= MAX_PODS:
            raise ValueError(f"the number of GPUs must be between 1 and {MAX_PODS}")
        await kv.set("max_pods", str(value))
        self._notice = f"up to {value} GPU{'s' if value != 1 else ''} at once"

    # ---------- lifecycle ----------

    async def start(self, *, run_loop: bool = True) -> None:
        """Claim leadership, or wait for it, then drain the queue.

        The lock is held on a dedicated connection for the life of the process.
        Two orchestrators would mean two sets of rented pods billing at once, so
        a process without the lock touches no GPU. But it keeps asking: a rolling
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
        self._strays_checked = False
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
            # rows with their cost and puts what was rendering back in the queue.
            # Every deploy used to leave a row open at $0, which is why the
            # cost history under-reported and the runs table filled with
            # sessions that never ended.
            try:
                await self._teardown("process stopping")
            except Exception:
                log.exception("shutdown during stop failed")
        for slot in self.slots:
            try:
                await slot.backend.aclose()
            except Exception:
                log.exception("closing pod %d's connections failed", slot.number)
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

    def _session_cost(self) -> float:
        return self._session_spent + sum(s.cost() for s in self.slots)

    def _slot_view(self, slot: PodSlot) -> dict[str, Any]:
        return {
            "number": slot.number,
            "state": slot.status.state,
            "detail": slot.status.detail,
            "pod_id": slot.status.pod_id,
            "uptime_s": round(slot.status.uptime_s, 1),
            "gpu": slot.gpu,
            "rate_per_hour": slot.rate,
            "cost_usd": round(slot.cost(), 4),
            "rendering": sum(1 for s in self._placed.values() if s is slot),
        }

    async def snapshot(self) -> dict[str, Any]:
        """The shared, admin-facing view."""
        session_s = time.time() - self._session_started if self._session_started else 0.0
        head = self._headline()
        pod = self._slot_view(head)
        return {
            "leader": self.leader,
            "policy": await self.policy(),
            "sage": await self.sage_enabled(),
            "pod": {k: pod[k] for k in
                    ("state", "detail", "pod_id", "uptime_s", "gpu", "rate_per_hour")},
            "pods": [self._slot_view(s) for s in self.slots if s.active or s.primary],
            "max_pods": await self.max_pods(),
            "max_pods_allowed": self.max_pods_allowed,
            "counts": await jobs.counts_all(),
            "inflight": len(self._inflight),
            "session": {
                "seconds": round(session_s, 1),
                "cost_usd": round(self._session_cost(), 4),
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
        head = self._headline().status
        state = head.state
        return {
            "pod": {
                "state": state,
                # The raw detail carries the GPU price, pod ids and exception
                # text; users get one fixed phrase per state instead.
                "detail": USER_POD_DETAIL.get(state, ""),
                "uptime_s": round(head.uptime_s, 1),
                "ready": sum(1 for s in self.slots if s.ready),
                "starting": sum(1 for s in self.slots
                                if not s.ready and s.status.state == "booting"),
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
        if not self._strays_checked:
            await self._adopt_strays()
        counts = await jobs.counts_all()
        want_work = counts["queued"] > 0 or bool(self._inflight)
        policy = await self.policy()

        if policy == "off":
            if any(s.active for s in self.slots):
                await self._teardown("policy off")
            await self._refresh()
            return

        if not want_work and policy == "auto":
            await self._refresh()
            await self._close_idle(queued=0, keep=0)
            return

        if not await self._ensure_pods(counts["queued"], policy):
            return

        if await self._enforce_ceilings():
            return

        await self._collect()
        await self._dispatch()

        queued = (await jobs.counts_all())["queued"]
        now = time.time()
        for slot in self.slots:
            if queued or any(s is slot for s in self._placed.values()):
                slot.last_busy = now
        await self._close_idle(queued=queued, keep=1 if policy == "keep-warm" else 0)

    async def _refresh(self) -> None:
        for slot in self.slots:
            if slot.boot is None:
                slot.status = await slot.backend.status()

    async def _close_idle(self, *, queued: int, keep: int) -> None:
        """Let go of every pod with nothing to do for `idle_shutdown_minutes`.

        Per pod, not all at once: the extra pods a long list called for are only
        worth their rate while that list lasts, even if one clip is still
        rendering on another. `keep` is how many stay regardless (keep-warm).
        """
        if queued:
            return
        limit = self.cfg.pod.idle_shutdown_minutes * 60
        now = time.time()
        idle = [s for s in self.slots
                if s.ready and not any(p is s for p in self._placed.values())
                and now - s.last_busy >= limit]
        if sum(1 for s in self.slots if s.active) > len(idle):
            keep = 0            # another pod is still up; it is the warm one
        for slot in sorted(idle, key=lambda s: s.number)[keep:]:
            await self._close_slot(slot, f"idle {self.cfg.pod.idle_shutdown_minutes} min")

    # ---------- bringing pods up ----------

    async def _open_run(self, slot: PodSlot, note: str) -> None:
        """Record the GPU session, once, however the pod came to be running.

        This used to happen only when the pod was first seen `off`, which missed
        every pod this process did not start itself: a start whose first
        connections timed out leaves a pod booting, the next tick adopts it, and
        the whole session then rendered without ever appearing in the cost
        history. The budget ceiling reads the backend's own clock and was never
        affected, but `runs` is what an admin actually looks at.
        """
        if slot.run_id is not None:
            return
        self._session_started = self._session_started or time.time()
        slot.run_id = await runs.start(slot.status.pod_id, slot.gpu or "?", note=note)

    async def _mark_run_ready(self, slot: PodSlot) -> None:
        if not slot.run_id:
            return
        await runs.update(slot.run_id, status="ready",
                          pod_id=slot.status.pod_id,
                          endpoint=slot.status.endpoint,
                          gpu_type=slot.gpu or "?")

    async def _adopt_strays(self) -> None:
        """Take over every pod a previous process left running, once per leadership.

        Otherwise a restart with nothing queued leaves them billing with no idle
        timer watching, and with work queued only the first is found - the rest
        run until someone reads the RunPod console.
        """
        self._strays_checked = True
        while len(self.slots) <= MAX_PODS:
            slot = next((s for s in self.slots
                         if not s.active and not s.status.pod_id), None)
            fresh = slot is None
            if fresh:
                if self._make_backend is None or len(self.slots) >= MAX_PODS:
                    return
                slot = self._new_slot()
            adopt = getattr(slot.backend, "adopt_existing", None)
            try:
                found = await adopt() if adopt is not None else None
            except Exception:
                log.exception("looking for pods left running failed")
                found = None
            if not found:
                if fresh:
                    await self._drop_slot(slot)
                return
            slot.status = await slot.backend.status()
            slot.last_busy = time.time()
            await self._open_run(slot, "adopted a pod left running")
            if slot.status.state == "ready":
                await self._mark_run_ready(slot)
            log.info("adopted pod %s as GPU %d", found, slot.number)

    def _new_slot(self) -> PodSlot:
        assert self._make_backend is not None
        taken = {s.number for s in self.slots}
        number = next(n for n in range(1, MAX_PODS + 2) if n not in taken)
        slot = PodSlot(self._make_backend(), number)
        self.slots.append(slot)
        return slot

    async def _drop_slot(self, slot: PodSlot) -> None:
        if slot.primary or slot not in self.slots:
            return
        self.slots.remove(slot)
        try:
            await slot.backend.aclose()
        except Exception:
            log.warning("closing pod %d's connections failed", slot.number)

    async def _backlog_minutes(self, gpu: str) -> float:
        """GPU minutes the queued clips need, at the shapes they asked for."""
        total = 0.0
        for row in await jobs.queued_shapes():
            preset = self.cfg.generation.preset(row.get("preset"))
            seconds = row.get("seconds") or self.cfg.generation.default_seconds
            total += estimate.minutes_per_clip(gpu, preset, seconds, self.cfg)
        return total

    async def _pods_wanted(self, queued: int, live: int) -> int:
        """One more pod than `live` while the queue would outlast its boot."""
        live = max(1, live)
        cap = await self.max_pods()
        if live >= cap or not queued:
            return live
        gpu = next((s.gpu for s in self.slots if s.gpu), "") or (
            self.cfg.runpod.gpu_preference[0] if self.cfg.runpod.gpu_preference else "")
        backlog = await self._backlog_minutes(gpu)
        if backlog / live > estimate.startup_minutes(self.cfg):
            return live + 1
        return live

    async def _ensure_pods(self, queued: int, policy: str) -> bool:
        """Get enough pods up for the work. True when at least one can render."""
        await self._refresh()
        for slot in self.slots:
            if slot.ready:
                # Already up, possibly from an earlier attempt or another process.
                if slot.run_id is None:
                    await self._open_run(slot, "adopted a running pod")
                    await self._mark_run_ready(slot)
                self._pod_came_up(slot)

        now = time.time()
        ready = [s for s in self.slots if s.ready]
        booting = [s for s in self.slots if s.boot is not None]
        waiting = [s for s in self.slots
                   if not s.ready and s.boot is None and now < s.retry_at]
        idle = [s for s in self.slots
                if not s.ready and s.boot is None and now >= s.retry_at]
        # A pod already on its way up is paid for: wait on it, never drop it.
        found = [s for s in idle if s.status.pod_id and s.status.state == "booting"]
        rest = sorted((s for s in idle if s not in found),
                      key=lambda s: (not s.status.pod_id, s.number))
        live = len(ready) + len(booting) + len(waiting) + len(found)
        need = await self._pods_wanted(queued, live) - live
        if not live:
            need = max(need, 1)

        starts: list[PodSlot] = found + rest[:max(0, need)]
        while len(starts) - len(found) < need and self._make_backend is not None \
                and len(self.slots) < await self.max_pods():
            starts.append(self._new_slot())
        # A pod that went away, or failed, and is not wanted any more closes its
        # session instead of billing in the background.
        for slot in rest[max(0, need):]:
            if slot.active:
                await self._close_slot(slot, "not needed")
            else:
                await self._drop_slot(slot)

        if not starts:
            return bool(ready)
        if ready or booting:
            for slot in starts:
                self._start_boot(slot)
            return bool(ready)
        # Nothing can render until a pod is up, so the first start is waited on
        # here and the others boot beside it.
        first, *others = starts
        for slot in others:
            self._start_boot(slot)
        return await self._boot(first)

    @staticmethod
    def _start_note(slot: PodSlot) -> str:
        state = slot.status.state
        if state in {"off", "error"} or not slot.status.pod_id:
            return "auto start"
        return f"adopted a pod that was {state}"

    def _start_boot(self, slot: PodSlot) -> None:
        note = self._start_note(slot)

        async def run() -> None:
            try:
                await self._boot(slot, note)
            finally:
                slot.boot = None
        # Marked booting before the task runs, so the next tick counts it.
        slot.status = PodStatus(state="booting", pod_id=slot.status.pod_id,
                                detail="starting the GPU")
        slot.boot = asyncio.create_task(run(), name=f"pod-{slot.number}-boot")

    async def _boot(self, slot: PodSlot, note: str | None = None) -> bool:
        if slot.run_id is None:
            if not any(s.ready for s in self.slots):
                self._notice = ("starting GPU - first boot downloads "
                                f"~{self.cfg.weights.total_gb_hint():.0f}GB of weights")
                self._user_notice = ("Starting the GPU. The first clip of a session "
                                     "takes a few minutes to begin.")
            await self._open_run(slot, note or self._start_note(slot))
        # The header reads the pod's status; from here until the pod answers it
        # must say "booting", not the "off" from before the start.
        slot.status = PodStatus(state="booting", detail="starting the GPU")

        def publish(st: PodStatus) -> None:
            slot.status = st

        try:
            slot.status = await slot.backend.ensure_ready(on_status=publish)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            label = "pod start failed" if len(self.slots) == 1 \
                else f"pod start failed (GPU {slot.number})"
            self._last_error = f"{label}: {str(e)[:300]}"
            slot.retry_at = time.time() + POD_RETRY_SECONDS
            if slot.run_id:
                await runs.update(slot.run_id, status="error", ended_at=time.time(),
                                  note=self._last_error)
                slot.run_id = None
            return False
        slot.last_busy = time.time()
        await self._mark_run_ready(slot)
        self._pod_came_up(slot)
        return True

    STARTING_ERROR = "pod start failed"
    STARTING_NOTICE = "starting GPU"
    STARTING_USER_NOTICE = "Starting the GPU"

    def _pod_came_up(self, slot: PodSlot) -> None:
        """Whatever was said while the pod started is no longer true.

        A start attempt that failed on one status poll left "pod start failed"
        on the admin page and "Starting the GPU" on everyone's feed for the
        whole session, because the pod then came up through the adopted-pod
        path above, which never cleared them. Only the starting messages are
        cleared here: an error from a render must stay visible.
        """
        slot.retry_at = 0.0
        if self._last_error.startswith(self.STARTING_ERROR):
            self._last_error = ""
        if self._notice.startswith(self.STARTING_NOTICE):
            self._notice = ""
        if self._user_notice.startswith(self.STARTING_USER_NOTICE):
            self._user_notice = ""

    async def _enforce_ceilings(self) -> bool:
        """Hard stops that exist so a bug cannot turn into a bill.

        Every pod of the session counts toward one limit, closed ones included.
        Returns True if it stopped everything.
        """
        cost = self._session_cost()
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

    # ---------- work ----------

    def _load(self, slot: PodSlot) -> int:
        return sum(1 for s in self._placed.values() if s is slot)

    def _free_slot(self) -> PodSlot | None:
        """The least busy pod that can take another prompt.

        One prompt per pod while there are several: a second one waiting in a
        busy pod's own queue would sit there while a pod that came up a minute
        later does nothing.
        """
        several = sum(1 for s in self.slots if s.active) > 1
        per_pod = 1 if several else MAX_INFLIGHT
        ready = [s for s in self.slots if s.ready and self._load(s) < per_pod]
        if not ready:
            return None
        return min(ready, key=lambda s: (self._load(s), s.number))

    async def _dispatch(self) -> None:
        while (slot := self._free_slot()) is not None:
            job = await jobs.claim_next_queued()
            if job is None:
                return
            backend = slot.backend
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
                    names.append(await backend.upload_image(data, name))
                keyframes = []
                for kf in job.get("keyframes") or []:
                    name = PurePosixPath(str(kf.get("key", ""))).name
                    stage = f"reading {name}"
                    data = await self.storage.get(kf["key"])
                    data, name = await asyncio.to_thread(
                        fit_reference, data, name, preset.width, preset.height)
                    stage = f"sending {name} to the GPU"
                    keyframes.append({**kf, "name": await backend.upload_image(data, name)})
                ref_video_names, ref_audio_names = [], []
                for key in job.get("ref_videos") or []:
                    name = PurePosixPath(str(key)).name
                    stage = f"reading {name}"
                    data = await self.storage.get(key)
                    stage = f"sending {name} to the GPU"
                    ref_video_names.append(await backend.upload_image(data, name))
                for key in job.get("ref_audios") or []:
                    name = PurePosixPath(str(key)).name
                    stage = f"reading {name}"
                    data = await self.storage.get(key)
                    stage = f"sending {name} to the GPU"
                    ref_audio_names.append(await backend.upload_image(data, name))
                audio_name = None
                if job.get("audio_key"):
                    name = PurePosixPath(str(job["audio_key"])).name
                    stage = f"reading {name}"
                    data = await self.storage.get(job["audio_key"])
                    stage = f"sending {name} to the GPU"
                    # Same door as the images: /upload/image writes any bytes.
                    audio_name = await backend.upload_image(data, name)
                stage = "submitting to the GPU"
                # The names ComfyUI stored, which is what the graph must reference.
                remote_id = await backend.submit(
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
            self._placed[job["id"]] = slot
            await jobs.update(job["id"], remote_id=remote_id)
            slot.last_busy = time.time()

    async def _collect(self) -> None:
        for job_id, remote_id in list(self._inflight.items()):
            slot = self._placed.get(job_id) or self.slots[0]
            job = await jobs.get_any(job_id)
            # Cancelled while it rendered. Until this check the GPU finished the
            # clip anyway, at full price, with one of the two slots held by a
            # job nobody wanted - which is what "the queue is stuck" was.
            if job is None or job["status"] != "running":
                await self._stop_render(job_id, remote_id)
                continue
            try:
                res = await slot.backend.poll(remote_id)
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
            slot.last_busy = time.time()
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

    def _forget(self, job_id: str) -> None:
        self._inflight.pop(job_id, None)
        self._placed.pop(job_id, None)
        self._poll_errors.pop(job_id, None)
        self._lost_polls.pop(job_id, None)

    async def _stop_render(self, job_id: str, remote_id: str) -> None:
        """The user no longer wants it: free the slot and tell the GPU."""
        slot = self._placed.get(job_id) or self.slots[0]
        self._forget(job_id)
        try:
            await slot.backend.cancel(remote_id)
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

    # ---------- taking pods down ----------

    async def _close_slot(self, slot: PodSlot, reason: str) -> None:
        """Stop one pod, close its session row, and requeue what it was rendering."""
        log.info("stopping GPU %d: %s", slot.number, reason)
        if slot.boot is not None and slot.boot is not asyncio.current_task():
            slot.boot.cancel()
            try:
                await slot.boot
            except BaseException:  # noqa: BLE001 - cancelled or failed, either way it's over
                pass
            slot.boot = None
        # Anything mid-render dies with the pod. Put it back in the queue rather
        # than leaving it 'running' until the process restarts - a running job
        # cannot be edited or removed.
        for job_id in [j for j, s in self._placed.items() if s is slot]:
            await jobs.update_if(job_id, "running", status="queued", remote_id=None)
            self._forget(job_id)
        cost = slot.cost()
        try:
            await slot.backend.shutdown()
        finally:
            if slot.run_id:
                await runs.update(slot.run_id, status="stopped",
                                  ended_at=time.time(),
                                  cost_estimate=round(cost, 4), note=reason)
                slot.run_id = None
            self._session_spent += cost
            slot.status = PodStatus(state="off", detail=reason)
            slot.last_busy = time.time()
            await self._drop_slot(slot)
            if any(s.active for s in self.slots):
                self._notice = f"GPU {slot.number} stopped ({reason})"
            else:
                self._end_session(reason)

    def _end_session(self, reason: str) -> None:
        self._notice = f"GPU stopped ({reason})"
        self._user_notice = "The GPU was stopped. Queued clips wait until it starts again."
        self._session_started = None
        self._session_spent = 0.0

    async def _teardown(self, reason: str) -> None:
        log.info("stopping every pod: %s", reason)
        # One pod refusing to die must not keep the others billing.
        first_error: Exception | None = None
        for slot in list(self.slots):
            try:
                await self._close_slot(slot, reason)
            except Exception as e:
                log.exception("stopping GPU %d failed", slot.number)
                first_error = first_error or e
        for job_id in list(self._inflight):
            await jobs.update_if(job_id, "running", status="queued", remote_id=None)
            self._forget(job_id)
        self._end_session(reason)
        if first_error is not None:
            raise first_error
