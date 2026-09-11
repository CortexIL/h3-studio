"""ComfyUI HTTP client.

ComfyUI ships a plain HTTP API, which is why this project needs no model code at all:
    POST /prompt            queue a workflow, get a prompt_id
    GET  /history/{id}      results once finished
    GET  /view              download an output file
    POST /upload/image      push a reference image
    GET  /queue             what is running / pending

We poll rather than use the websocket: polling survives reconnects and pod restarts,
which matters more here than sub-second progress updates.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, endpoint: str, *, timeout: float = 60.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.client_id = uuid.uuid4().hex
        self._http = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._models: dict[str, list[str]] | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def is_alive(self) -> bool:
        try:
            r = await self._http.get(f"{self.endpoint}/system_stats", timeout=10.0)
            return r.status_code == 200
        except Exception:
            return False

    async def object_info(self, node: str | None = None) -> dict[str, Any]:
        """Used to verify the H3 nodes actually exist before queueing anything."""
        url = f"{self.endpoint}/object_info" + (f"/{node}" if node else "")
        r = await self._http.get(url)
        r.raise_for_status()
        return r.json()

    async def upload_image(self, data: bytes, name: str, *,
                           overwrite: bool = True) -> str:
        files = {"image": (name, data, "application/octet-stream")}
        form = {"overwrite": "true" if overwrite else "false", "type": "input"}
        r = await self._http.post(f"{self.endpoint}/upload/image", files=files,
                                  data=form)
        r.raise_for_status()
        payload = r.json()
        got = payload.get("name") or name
        sub = payload.get("subfolder") or ""
        return f"{sub}/{got}" if sub else got

    async def model_options(self) -> dict[str, list[str]]:
        """Every model filename ComfyUI will accept, keyed by loader input name.

        Cached: the answer only changes when the pod restarts, and this is consulted
        on every submit.
        """
        if self._models is not None:
            return self._models
        info = await self.object_info()
        out: dict[str, list[str]] = {}
        for node in info.values():
            required = (node.get("input") or {}).get("required") or {}
            for field, spec in required.items():
                if not field.endswith("_name"):
                    continue
                if isinstance(spec, list) and spec and isinstance(spec[0], list):
                    out.setdefault(field, [])
                    for name in spec[0]:
                        if isinstance(name, str) and name not in out[field]:
                            out[field].append(name)
        self._models = out
        return out

    async def queue_prompt(self, workflow: dict[str, Any]) -> str:
        body = {"prompt": workflow, "client_id": self.client_id}
        r = await self._http.post(f"{self.endpoint}/prompt", json=body)
        if r.status_code >= 400:
            # ComfyUI returns a structured validation error; surfacing it verbatim
            # is the difference between a 5-minute fix and an hour of guessing.
            raise ComfyError(f"/prompt {r.status_code}: {r.text[:2000]}")
        return r.json()["prompt_id"]

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        r = await self._http.get(f"{self.endpoint}/history/{prompt_id}")
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get(prompt_id)

    async def queue_state(self) -> tuple[int, int]:
        """(running, pending)"""
        try:
            r = await self._http.get(f"{self.endpoint}/queue", timeout=15.0)
            r.raise_for_status()
            d = r.json()
            return len(d.get("queue_running", [])), len(d.get("queue_pending", []))
        except Exception:
            return 0, 0

    async def download(self, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        params = {"filename": filename, "subfolder": subfolder, "type": type_}
        r = await self._http.get(f"{self.endpoint}/view", params=params)
        r.raise_for_status()
        return r.content

    @staticmethod
    def find_video_outputs(hist: dict[str, Any]) -> list[dict[str, str]]:
        """Pull video/gif file refs out of a history entry, whatever node produced them."""
        found: list[dict[str, str]] = []
        for node_out in (hist.get("outputs") or {}).values():
            for key in ("videos", "gifs", "images", "files"):
                for item in node_out.get(key, []) or []:
                    fn = item.get("filename", "")
                    if fn.lower().endswith((".mp4", ".webm", ".mov", ".mkv")):
                        found.append({
                            "filename": fn,
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                        })
        return found

    @staticmethod
    def status_of(hist: dict[str, Any]) -> tuple[str, str | None]:
        """-> ('done'|'failed'|'running', error_message)"""
        st = hist.get("status") or {}
        if st.get("status_str") == "error" or st.get("completed") is False and st.get("messages"):
            for kind, payload in st.get("messages", []):
                if kind == "execution_error":
                    detail = payload if isinstance(payload, dict) else {}
                    msg = detail.get("exception_message") or json.dumps(detail)[:500]
                    return "failed", msg
        if st.get("completed"):
            return "done", None
        return "running", None
