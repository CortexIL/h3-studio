"""RunPod pod lifecycle + ComfyUI inference, wired together as one Backend.

Cost model note: RunPod bills per second, so there is no hour minimum - a ten
minute session costs ten minutes. That is what makes "turn it on for two clips"
viable rather than wasteful.

The endpoints below target RunPod's REST API (rest.runpod.io/v1). Field names do
drift between API versions; `python -m app.doctor` checks them against your account
before a real batch depends on them.
"""
from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import Config
from ..workflows import build_workflow
from . import JobResult, PodStatus
from .comfy import ComfyClient, ComfyError

API = "https://rest.runpod.io/v1"
COMFY_PORT = 8188

# Rough per-hour rates, used only for the live cost readout when the API does not
# report one. Real billing always comes from RunPod.
FALLBACK_RATES = {
    "NVIDIA GeForce RTX 5090": 0.99,
    "NVIDIA L40S": 0.86,
    "NVIDIA RTX A6000": 0.49,
    "NVIDIA H100 PCIe": 2.89,
    "NVIDIA A100 80GB PCIe": 1.39,
}


def _bootstrap_cmd(cfg: Config) -> list[str]:
    """Download the pruned weights, then start ComfyUI.

    Returned as argv, not a string: RunPod's schema types dockerStartCmd as an array
    and rejects a string outright (usefully, at validation time, before any pod is
    billed).

    Deliberately re-downloads each session instead of using a persistent volume:
    ~40GB takes 2-5 minutes and costs a few cents, versus ~$10/month for a network
    volume that bills even while the pod is off.
    """
    lines = [
        "set -eu",
        # Where ComfyUI lives differs between image builds, so find it rather than
        # assume. Guessing wrong only surfaces minutes in, on a pod that is billing.
        'COMFY=""',
        'for d in /workspace/ComfyUI /comfyui /ComfyUI /opt/ComfyUI /root/ComfyUI; do',
        '  if [ -f "$d/main.py" ]; then COMFY="$d"; break; fi',
        'done',
        'if [ -z "$COMFY" ]; then',
        '  found=$(find / -maxdepth 5 -name main.py -ipath "*comfy*" 2>/dev/null | head -1)',
        '  [ -n "$found" ] && COMFY=$(dirname "$found")',
        'fi',
        'if [ -z "$COMFY" ]; then echo "[h3studio] ComfyUI not found in image"; exit 1; fi',
        'echo "[h3studio] ComfyUI at $COMFY"',
        'cd "$COMFY"',
        "pip install -q --no-cache-dir 'huggingface_hub[cli]' >/dev/null 2>&1 || true",
        "echo '[h3studio] downloading weights'",
    ]
    for f in cfg.weights.files:
        repo, src, dst = shlex.quote(cfg.weights.repo), shlex.quote(f.src), f.dst
        lines.append(
            f'hf download {repo} {src} --local-dir "$COMFY/{dst}" || '
            f'huggingface-cli download {repo} {src} --local-dir "$COMFY/{dst}"'
        )
    lines += [
        "echo '[h3studio] weights ready'",
        f'python main.py --listen 0.0.0.0 --port {COMFY_PORT}',
    ]
    return ["bash", "-lc", "\n".join(lines)]


class RunpodError(RuntimeError):
    """A RunPod API failure, carrying enough detail to decide whether to retry."""
    status: int = 0
    body: str = ""

    @property
    def is_capacity(self) -> bool:
        """No hardware free right now - a different GPU or a later attempt may work."""
        return self.status >= 500 or "no instances currently available" in self.body.lower()

    @property
    def is_client_error(self) -> bool:
        """A malformed or unauthorised request. Retrying other GPUs cannot help."""
        return 400 <= self.status < 500


class RunpodBackend:
    name = "runpod"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._http = httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {cfg.runpod.api_key}",
                "Content-Type": "application/json",
            },
        )
        self._pod_id: str | None = None
        self._started_at: float | None = None
        self._rate_per_hour: float = 0.0
        self._gpu_used: str = ""
        self._comfy: ComfyClient | None = None
        self._detail = ""

    # ---------- plumbing ----------

    async def aclose(self) -> None:
        if self._comfy:
            await self._comfy.aclose()
        await self._http.aclose()

    def _endpoint(self) -> str | None:
        if not self._pod_id:
            return None
        return f"https://{self._pod_id}-{COMFY_PORT}.proxy.runpod.net"

    async def _api(self, method: str, path: str, **kw: Any) -> Any:
        r = await self._http.request(method, f"{API}{path}", **kw)
        if r.status_code >= 400:
            err = RunpodError(f"RunPod {method} {path} -> {r.status_code}: {r.text[:800]}")
            err.status = r.status_code
            err.body = r.text
            raise err
        return r.json() if r.content else {}

    def cost_so_far(self) -> float:
        if not self._started_at:
            return 0.0
        return (time.time() - self._started_at) / 3600.0 * self._rate_per_hour

    @property
    def rate_per_hour(self) -> float:
        return self._rate_per_hour

    @property
    def gpu_used(self) -> str:
        return self._gpu_used

    # ---------- lifecycle ----------

    async def status(self) -> PodStatus:
        if not self._pod_id:
            return PodStatus(state="off", detail=self._detail)
        uptime = time.time() - self._started_at if self._started_at else 0.0
        try:
            pod = await self._api("GET", f"/pods/{self._pod_id}")
        except Exception as e:
            return PodStatus(state="error", pod_id=self._pod_id, detail=str(e)[:300],
                             uptime_s=uptime)
        desired = str(pod.get("desiredStatus") or pod.get("status") or "").upper()
        if desired in {"TERMINATED", "EXITED"}:
            return PodStatus(state="off", pod_id=self._pod_id, uptime_s=uptime)
        if desired != "RUNNING":
            return PodStatus(state="booting", pod_id=self._pod_id, uptime_s=uptime,
                             detail=f"pod {desired.lower()}")
        # RUNNING is not the same as ready - ComfyUI still has ~40GB to fetch.
        if self._comfy and await self._comfy.is_alive():
            return PodStatus(state="ready", endpoint=self._endpoint(), pod_id=self._pod_id,
                             uptime_s=uptime, detail=self._detail or "ready")
        return PodStatus(state="booting", pod_id=self._pod_id, uptime_s=uptime,
                         detail="downloading weights / starting ComfyUI")

    async def ensure_ready(self) -> PodStatus:
        if self._pod_id is None:
            await self._create()
        self._comfy = self._comfy or ComfyClient(self._endpoint() or "")
        deadline = time.time() + self.cfg.pod.boot_timeout_minutes * 60
        while time.time() < deadline:
            st = await self.status()
            if st.state == "ready":
                return st
            if st.state in {"off", "error"}:
                raise RuntimeError(f"pod failed to start: {st.detail}")
            await asyncio.sleep(5)
        raise TimeoutError(
            f"pod not ready after {self.cfg.pod.boot_timeout_minutes} min - "
            "usually a slow weight download or a bad image"
        )

    def build_create_body(self, gpu: str) -> dict[str, Any]:
        """The exact POST /pods payload. Split out so `app.doctor` can validate it
        against RunPod's schema without creating anything."""
        rp = self.cfg.runpod
        body: dict[str, Any] = {
            "name": f"h3studio-{int(time.time())}",
            "imageName": rp.image,
            "gpuTypeIds": [gpu],
            "gpuCount": 1,
            "cloudType": rp.cloud_type,
            "containerDiskInGb": rp.container_disk_gb,
            # No pod volume. RunPod otherwise attaches 20GB at /workspace, which
            # would both mask a ComfyUI installed there and be far too small for
            # ~40GB of weights. The container disk holds everything instead.
            "volumeInGb": 0,
            "ports": [f"{COMFY_PORT}/http", "22/tcp"],
            "dockerStartCmd": _bootstrap_cmd(self.cfg),
            "interruptible": rp.interruptible,
        }
        if rp.network_volume_id:
            body["networkVolumeId"] = rp.network_volume_id
        if rp.data_center_ids:
            body["dataCenterIds"] = rp.data_center_ids
        return body

    async def _create(self) -> None:
        rp = self.cfg.runpod
        last_err: Exception | None = None
        tried: list[str] = []
        for gpu in rp.gpu_preference:
            body = self.build_create_body(gpu)
            try:
                pod = await self._api("POST", "/pods", json=body)
            except RunpodError as e:
                if e.is_client_error:
                    # Rejected the request itself - every other GPU would be rejected
                    # identically, so fail now with the reason instead of three times
                    # over with the last one.
                    raise
                last_err = e
                tried.append(gpu)
                self._detail = f"{gpu} has no capacity, trying next"
                continue
            except Exception as e:
                last_err = e
                tried.append(gpu)
                continue
            self._pod_id = pod.get("id") or pod.get("podId")
            self._started_at = time.time()
            self._gpu_used = gpu
            self._rate_per_hour = float(pod.get("costPerHr") or FALLBACK_RATES.get(gpu, 1.0))
            self._detail = f"{gpu} @ ${self._rate_per_hour:.2f}/hr"
            return
        capacity = isinstance(last_err, RunpodError) and last_err.is_capacity
        if capacity:
            raise RuntimeError(
                f"None of {tried} had free capacity on {rp.cloud_type} cloud just now. "
                f"This is availability, not configuration - try again shortly, add more "
                f"cards to runpod.gpu_preference, or set cloud_type: SECURE (dearer, "
                f"usually available). Last response: {last_err}"
            )
        raise RuntimeError(f"could not create a pod ({tried}): {last_err}")

    async def shutdown(self) -> None:
        if not self._pod_id:
            return
        pod_id, self._pod_id = self._pod_id, None
        try:
            await self._api("DELETE", f"/pods/{pod_id}")
        finally:
            self._started_at = None
            if self._comfy:
                await self._comfy.aclose()
                self._comfy = None

    # ---------- inference ----------

    async def upload_image(self, local_path: Path) -> str:
        if not self._comfy:
            raise RuntimeError("pod not ready")
        return await self._comfy.upload_image(local_path)

    async def submit(self, job: dict[str, Any]) -> str:
        if not self._comfy:
            raise RuntimeError("pod not ready")
        return await self._comfy.queue_prompt(build_workflow(job, self.cfg))

    async def poll(self, remote_id: str) -> JobResult:
        if not self._comfy:
            return JobResult(state="failed", error="pod not ready")
        try:
            hist = await self._comfy.history(remote_id)
        except Exception as e:
            # A blip talking to the proxy is not a failed job.
            return JobResult(state="running", meta={"transient_error": str(e)[:200]})
        if hist is None:
            return JobResult(state="pending")
        state, err = ComfyClient.status_of(hist)
        if state == "failed":
            return JobResult(state="failed", error=err)
        if state != "done":
            return JobResult(state="running")
        outs = ComfyClient.find_video_outputs(hist)
        if not outs:
            return JobResult(state="failed", error="workflow finished but produced no video")
        o = outs[-1]
        try:
            data = await self._comfy.download(o["filename"], o["subfolder"], o["type"])
        except (httpx.HTTPError, ComfyError) as e:
            return JobResult(state="failed", error=f"download failed: {e}")
        return JobResult(state="done", progress=1.0, video=data, filename=o["filename"],
                         meta={"gpu": self._gpu_used})
