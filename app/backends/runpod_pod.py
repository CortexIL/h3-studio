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
import logging
import re
import shlex
import time
import uuid
from typing import Any

import httpx

from ..config import Config
from ..workflows import build_workflow, normalize_models
from . import JobResult, PodStatus
from .comfy import ComfyClient, ComfyError

log = logging.getLogger("h3studio.runpod")

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


# RunPod's enum, newest first. A host is acceptable only if its driver supports at
# least the CUDA version the image's PyTorch was built against.
CUDA_VERSIONS = ["13.0", "12.9", "12.8", "12.7", "12.6", "12.5",
                 "12.4", "12.3", "12.2", "12.1", "12.0", "11.8"]


def allowed_cuda_for(image: str) -> list[str]:
    """Which host CUDA versions can run this image.

    Left unset, RunPod will happily place the pod on a machine whose driver is too
    old for the image's torch build - which is exactly what happened: a cu130 image
    landed on a driver supporting only 12.4, and ComfyUI died on import with
    "The NVIDIA driver on your system is too old". Nothing in the pod request said
    otherwise, so RunPod was not at fault.
    """
    match = re.search(r"cuda(\d+)\.(\d+)", image)
    if not match:
        return []                      # unknown image naming: do not over-constrain
    needed = (int(match.group(1)), int(match.group(2)))
    return [v for v in CUDA_VERSIONS
            if tuple(int(p) for p in v.split(".")) >= needed]


# Headroom above the models themselves: ComfyUI, torch, the CUDA context and the
# decoded latents all need somewhere to live.
RAM_HEADROOM_GB = 16


def min_ram_for(cfg: Config) -> int:
    """System RAM the pod must have, in GB.

    ComfyUI stages a model through system RAM before it reaches the GPU, so the host
    needs room for the two largest checkpoints at once. RunPod defaults minRAMPerGPU
    to 8GB, and leaving it unset is how a 5090 pod ended up with 46GB for 48GB of
    weights: the GPU sat at 3.7GB of 34 while the machine swapped to disk, and a clip
    estimated at 4 minutes had not moved after 29.
    """
    sizes = sorted((f.gb or 0) for f in cfg.weights.files)
    two_largest = sum(sizes[-2:])
    return max(16, int(two_largest + RAM_HEADROOM_GB + 0.5))


STATUS_FILE = "/tmp/h3_status"

# Binds :8188 immediately and answers 503 with whatever the bootstrap last wrote.
# Without this the port simply refuses connections for the entire download, so a
# script that died in its first seconds is indistinguishable from a slow 56GB fetch -
# which is exactly how one pod burned 15 minutes and ~$0.20 doing nothing at all.
_STATUS_SERVER = f"""
import http.server, socketserver, json
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            msg = open({STATUS_FILE!r}).read().strip()
        except OSError:
            msg = "starting"
        body = json.dumps({{"h3studio_bootstrap": msg}}).encode()
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
socketserver.TCPServer.allow_reuse_address = True
socketserver.TCPServer(("0.0.0.0", {COMFY_PORT}), H).serve_forever()
"""


def _bootstrap_cmd(cfg: Config) -> list[str]:
    """Download the weights, then hand over to the image's own start script.

    Used as dockerEntrypoint, not dockerStartCmd. The image declares
    ENTRYPOINT ["/start.sh"] with no CMD, so anything passed as dockerStartCmd
    arrives as arguments to /start.sh and is silently ignored - which cost one
    whole pod boot to discover.

    Note there is no `set -e`. A failing step parks the pod with a readable status
    instead of exiting, because a container that dies is reported by RunPod exactly
    like one that is still working - the failure has to stay visible to be fixed.

    Weights are re-downloaded each session rather than kept on a network volume:
    a few cents of transfer against ~$10/month for a disk billed even while off.
    """
    stage = "/workspace/h3models"
    lines = [
        "set -u",
        f"echo starting > {STATUS_FILE}",
        f"cat > /tmp/h3_status_server.py <<'H3PY'{_STATUS_SERVER}H3PY",
        "python3 /tmp/h3_status_server.py &",
        "STATUS_PID=$!",
        "",
        f'note() {{ echo "$1" > {STATUS_FILE}; echo "[h3studio] $1"; }}',
        # Park rather than exit, so the status stays readable at the same URL.
        'die() { note "FAILED: $1"; sleep 86400; }',
        "",
        'note "installing huggingface cli"',
        "pip install -q --no-cache-dir 'huggingface_hub[cli]' >/dev/null 2>&1 || true",
        'DL=""',
        'command -v hf >/dev/null 2>&1 && DL=hf',
        '[ -z "$DL" ] && command -v huggingface-cli >/dev/null 2>&1 && DL=huggingface-cli',
        '[ -z "$DL" ] && die "no huggingface downloader available"',
        "",
        f"mkdir -p {stage}",
    ]

    total = len(cfg.weights.files)
    kinds = set()
    for i, f in enumerate(cfg.weights.files, 1):
        repo, src = shlex.quote(cfg.weights.repo), shlex.quote(f.src)
        name = f.src.rsplit("/", 1)[-1]
        kind = f.src.split("/", 1)[0]            # diffusion_models/x.safetensors -> diffusion_models
        kinds.add(kind)
        # Downloaded into the stage root, not into stage/<kind>. huggingface-cli keeps
        # the repo's own directory structure inside --local-dir, so pointing it at the
        # subfolder produced stage/vae/vae/file.safetensors - one level too deep. The
        # models still loaded, but ComfyUI listed them as "vae/file.safetensors", which
        # matches no template default and shows up as five missing-model errors.
        # The repo's top-level folders already match ComfyUI's own names, so unpacking
        # at the root lines everything up by itself.
        expect = int((f.gb or 0) * 1e9 * 0.98)   # allow for GB vs GiB reporting
        target = f'{stage}/{f.src}'
        lines += [
            f'if [ -f "{target}" ] && [ "$(stat -c%s "{target}" 2>/dev/null || echo 0)" '
            f'-ge {expect} ]; then',
            f'  note "already present {i}/{total}: {name}"',
            'else',
            f'  note "downloading {i}/{total}: {name}"',
            f'  $DL download {repo} {src} --local-dir "{stage}" '
            f'|| die "download failed: {name}"',
            'fi',
        ]

    # extra_model_paths.yaml is ComfyUI's supported way to use models stored outside
    # its tree, so nothing has to be copied into a directory the image manages.
    yaml_lines = ["h3studio:", f"    base_path: {stage}"]
    yaml_lines += [f"    {k}: {k}" for k in sorted(kinds)]
    paths_yaml = "\n".join(yaml_lines)

    lines += [
        "",
        f"cat > {stage}/paths.yaml <<'H3YAML'\n{paths_yaml}\nH3YAML",
        # The image reads this file and appends it to ComfyUI's own arguments, which
        # is why we work through it rather than launching ComfyUI ourselves: /start.sh
        # also sets up FileBrowser, Jupyter and the venv, and replacing it broke all
        # of that silently.
        'ARGS=/workspace/runpod-slim/comfyui_args.txt',
        'mkdir -p "$(dirname $ARGS)"',
        f'echo "--extra-model-paths-config {stage}/paths.yaml" > $ARGS',
        "",
        # Release :8188 before /start.sh launches ComfyUI, or ComfyUI cannot bind and
        # the pod looks healthy while serving nothing but progress messages.
        'note "weights ready, handing over to the image start script"',
        'kill $STATUS_PID 2>/dev/null || true',
        'sleep 2',
        'exec /start.sh',
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
        # RUNNING is not the same as ready - the weights are still downloading.
        if self._comfy and await self._comfy.is_alive():
            return PodStatus(state="ready", endpoint=self._endpoint(), pod_id=self._pod_id,
                             uptime_s=uptime, detail=self._detail or "ready")
        detail = await self._bootstrap_progress()
        # A bootstrap that failed parks itself and keeps saying so. Surfacing that as
        # an error rather than "still booting" is the difference between a visible
        # problem and a pod quietly billing for twenty minutes.
        state = "error" if detail.startswith("FAILED") else "booting"
        return PodStatus(state=state, pod_id=self._pod_id, uptime_s=uptime, detail=detail)

    async def _bootstrap_progress(self) -> str:
        """Ask the pod's bootstrap what it is doing.

        Until ComfyUI takes the port, a tiny server there answers 503 with the current
        step. Without it the port simply refuses connections and there is no way to
        tell a stalled download from a script that died in its first seconds.
        """
        endpoint = self._endpoint()
        if not endpoint:
            return "starting"
        try:
            # A separate client on purpose: self._http carries the RunPod API key in
            # its default headers, and the pod proxy has no business receiving it.
            async with httpx.AsyncClient(timeout=10.0) as probe:
                r = await probe.get(f"{endpoint}/system_stats")
            if r.status_code == 503:
                msg = r.json().get("h3studio_bootstrap")
                if msg:
                    return str(msg)[:200]
        except (httpx.HTTPError, ValueError):
            pass
        return "starting up (no status yet)"

    POD_NAME_PREFIX = "h3studio-"

    async def adopt_existing(self) -> str | None:
        """Take over a pod this app already started, instead of making another.

        Without this, restarting the app - or having run the smoketest first - starts
        a *second* pod while the first keeps billing, and re-downloads 56GB it does
        not need. The pod name prefix is the marker; anything else on the account is
        left alone.
        """
        try:
            data = await self._api("GET", "/pods")
        except Exception:
            return None
        pods = data if isinstance(data, list) else data.get("data", [])
        for pod in pods:
            name = str(pod.get("name") or "")
            state = str(pod.get("desiredStatus") or pod.get("status") or "").upper()
            if not name.startswith(self.POD_NAME_PREFIX) or state != "RUNNING":
                continue
            self._pod_id = str(pod.get("id"))
            self._gpu_used = self._gpu_from(pod)
            self._rate_per_hour = float(pod.get("costPerHr")
                                        or FALLBACK_RATES.get(self._gpu_used, 1.0))
            # Bill from adoption, not from the pod's real start: this backend cannot
            # know what the earlier session already spent, and quietly inheriting an
            # unknown amount would make the budget ceiling meaningless.
            self._started_at = time.time()
            self._detail = f"adopted running pod {self._pod_id}"
            self._comfy = ComfyClient(self._endpoint() or "")
            return self._pod_id
        return None

    @staticmethod
    def _gpu_from(pod: dict[str, Any]) -> str:
        machine = pod.get("machine") or {}
        for key in ("gpuDisplayName", "gpuTypeId", "gpuType"):
            value = machine.get(key) or pod.get(key)
            if isinstance(value, str) and value:
                return value
        return "?"

    async def ensure_ready(self) -> PodStatus:
        if self._pod_id is None:
            if await self.adopt_existing():
                log.info("reusing existing pod %s", self._pod_id)
            else:
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

    def build_create_body(self, gpu: str, cloud: str | None = None) -> dict[str, Any]:
        """The exact POST /pods payload. Split out so `app.doctor` can validate it
        against RunPod's schema without creating anything."""
        rp = self.cfg.runpod
        body: dict[str, Any] = {
            # Unique per attempt, not just per second: `_claim_stray` finds a pod a
            # failed create left behind by matching this name exactly, and two
            # attempts inside one second would otherwise be indistinguishable.
            "name": f"h3studio-{int(time.time())}-{uuid.uuid4().hex[:6]}",
            "imageName": rp.image,
            "gpuTypeIds": [gpu],
            "gpuCount": 1,
            "cloudType": cloud or rp.cloud_type,
            "containerDiskInGb": rp.container_disk_gb,
            # No pod volume. RunPod otherwise attaches 20GB at /workspace, which
            # would both mask a ComfyUI installed there and be far too small for
            # tens of GB of weights. The container disk holds everything instead.
            "volumeInGb": 0,
            "ports": [f"{COMFY_PORT}/http", "22/tcp"],
            "dockerEntrypoint": _bootstrap_cmd(self.cfg),
            "interruptible": rp.interruptible,
        }
        cuda = rp.allowed_cuda_versions or allowed_cuda_for(rp.image)
        if cuda:
            body["allowedCudaVersions"] = cuda
        body["minRAMPerGPU"] = rp.min_ram_gb or min_ram_for(self.cfg)
        if rp.network_volume_id:
            body["networkVolumeId"] = rp.network_volume_id
        if rp.data_center_ids:
            body["dataCenterIds"] = rp.data_center_ids
        return body

    async def _create(self) -> None:
        """Take the first card with capacity, cheapest cloud first.

        "There are no instances currently available" is RunPod's answer for a
        card nobody has free right now, and it arrives as a 500. It says nothing
        about the request, so the only useful response is to ask for a different
        card - and, once the whole preference list is exhausted, a different
        cloud. Secure capacity costs more per hour than community, which is why
        it is the fallback rather than the default.
        """
        rp = self.cfg.runpod
        last_err: Exception | None = None
        tried: list[str] = []
        clouds = [rp.cloud_type]
        if rp.cloud_fallback and rp.cloud_fallback != rp.cloud_type:
            clouds.append(rp.cloud_fallback)
        for cloud in clouds:
            for gpu in rp.gpu_preference:
                body = self.build_create_body(gpu, cloud)
                name = str(body["name"])
                try:
                    pod = await self._api("POST", "/pods", json=body)
                except RunpodError as e:
                    if e.is_client_error:
                        # Rejected the request itself - every other GPU would be
                        # rejected identically, so fail now with the reason instead
                        # of once per card with the last one.
                        raise
                    if await self._claim_stray(name, gpu):
                        return
                    last_err = e
                    tried.append(f"{gpu} on {cloud}")
                    self._detail = f"{gpu} has no capacity on {cloud}, trying next"
                    continue
                except Exception as e:
                    if await self._claim_stray(name, gpu):
                        return
                    last_err = e
                    tried.append(f"{gpu} on {cloud}")
                    continue
                self._pod_id = pod.get("id") or pod.get("podId")
                self._started_at = time.time()
                self._gpu_used = gpu
                self._rate_per_hour = float(pod.get("costPerHr") or FALLBACK_RATES.get(gpu, 1.0))
                self._detail = f"{gpu} on {cloud} @ ${self._rate_per_hour:.2f}/hr"
                return
        capacity = isinstance(last_err, RunpodError) and last_err.is_capacity
        if capacity:
            raise RuntimeError(
                f"No capacity for any of {tried} just now. This is availability, not "
                f"configuration - it usually clears within the hour. Add cards to "
                f"runpod.gpu_preference for more chances. Last response: {last_err}"
            )
        raise RuntimeError(f"could not create a pod ({tried}): {last_err}")

    async def _claim_stray(self, name: str, gpu: str) -> bool:
        """Take over a pod the failed create may have started anyway.

        POST /pods is not atomic from here. A read timeout, a dropped connection
        or a 502 after RunPod has already begun renting leaves a GPU billing that
        this process holds no id for, so no teardown, ceiling or idle timer can
        ever reach it - only a human reading the RunPod console. It has happened:
        the app reported that none of three cards had capacity while a pod it had
        just asked for ran for four minutes at $0.69/hr.

        The name is unique per attempt, so a pod wearing it is ours and nobody
        else's. Adopt rather than terminate: a pod was wanted, one exists, and
        billing has started either way.
        """
        try:
            data = await self._api("GET", "/pods")
        except Exception:
            return False
        pods = data if isinstance(data, list) else data.get("data", [])
        for pod in pods:
            if str(pod.get("name") or "") != name:
                continue
            self._pod_id = str(pod.get("id"))
            self._started_at = time.time()
            found = self._gpu_from(pod)
            self._gpu_used = gpu if found == "?" else found
            self._rate_per_hour = float(pod.get("costPerHr")
                                        or FALLBACK_RATES.get(self._gpu_used, 1.0))
            self._detail = f"recovered {self._gpu_used} from a failed create"
            log.warning("create reported failure but left pod %s running; adopted it",
                        self._pod_id)
            return True
        return False

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

    async def upload_image(self, data: bytes, name: str) -> str:
        if not self._comfy:
            raise RuntimeError("pod not ready")
        return await self._comfy.upload_image(data, name)

    async def submit(self, job: dict[str, Any]) -> str:
        if not self._comfy:
            raise RuntimeError("pod not ready")
        graph = build_workflow(job, self.cfg)
        # Reconcile the template's model filenames with what this pod actually has,
        # rather than trusting names captured whenever the template was exported.
        try:
            swapped = normalize_models(graph, await self._comfy.model_options())
            for old, new in swapped:
                log.info("workflow: %s -> %s", old, new)
        except Exception as e:
            log.warning("could not check model names against ComfyUI: %s", e)
        return await self._comfy.queue_prompt(graph)

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
