"""MCP server: lets Claude Desktop (or any MCP client) drive H3 Studio.

This is a thin client over the app's own HTTP API - it holds no state and duplicates
no logic. That matters: the web page and Claude go through exactly the same endpoints,
so they can never disagree about what is queued or what it costs.

The app must be running. If it is not, every tool returns a plain instruction to
start it rather than a stack trace, because the person reading that message is
Claude relaying to a user, not a developer.

Registered with Claude Desktop by `python -m app.install_mcp`.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from . import config as config_mod

mcp = MCPServer(
    "h3-studio",
    instructions=(
        "Generate videos with MiniMax H3 on a rented GPU via the user's local "
        "H3 Studio app. Queue prompts, attach reference images, and check progress. "
        "Generation costs real money, so call estimate_cost and tell the user the "
        "figure before queueing a large batch."
    ),
)

_cfg = config_mod.load()
BASE = f"http://{_cfg.server.host}:{_cfg.server.port}"
NOT_RUNNING = (
    "H3 Studio is not running. Ask the user to start it by double-clicking the "
    "'H3 Studio' shortcut on their desktop, then try again."
)


# Private sentinel, deliberately not "error": the app's own status payload carries
# an "error" field of its own, so using that key here made every successful call look
# like a failure.
FAILED = "__h3_call_failed__"


def _fail(message: str) -> dict[str, Any]:
    return {FAILED: True, "error": message}


def _failed(r: Any) -> bool:
    return isinstance(r, dict) and r.get(FAILED) is True


def _clean(r: dict[str, Any]) -> dict[str, Any]:
    """Strip the sentinel before handing a failure back to the model."""
    return {k: v for k, v in r.items() if k != FAILED}


async def _call(method: str, path: str, **kw: Any) -> Any:
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, f"{BASE}{path}", **kw)
            if r.status_code >= 400:
                detail = r.json().get("detail") if r.content else r.reason_phrase
                return _fail(str(detail))
            return r.json()
    except (httpx.ConnectError, httpx.ReadTimeout):
        return _fail(NOT_RUNNING)


@mcp.tool(description="Check whether H3 Studio is running, the GPU state, queue "
                      "counts and how much the current session has cost so far.")
async def get_status() -> dict[str, Any]:
    s = await _call("GET", "/api/status")
    if _failed(s):
        return _clean(s)
    return {
        "running": True,
        "demo_mode": s["config"]["mock"],
        "gpu": {"state": s["pod"]["state"], "type": s["pod"]["gpu"] or None,
                "detail": s["pod"]["detail"]},
        "queue": s["counts"],
        "session_cost_usd": s["session"]["cost_usd"],
        "budget_limit_usd": s["session"]["limit_usd"],
        "output_folder": s["output_folder"],
        "inbox_folder": s.get("inbox_folder"),
    }


@mcp.tool(description="Estimate time and cost for a batch BEFORE queueing it. "
                      "Call this first whenever the user asks for more than a "
                      "couple of clips, and report the dollar figure back to them.")
async def estimate_cost(prompts: list[str], seconds: int = 10,
                        preset: str = "draft", takes: int = 1) -> dict[str, Any]:
    return await _call("POST", "/api/estimate", json={
        "prompts": "\n".join(prompts), "seconds": seconds,
        "preset": preset, "count": takes,
    })


@mcp.tool(description="Queue one or more videos. 'preset' is 'draft' (768x432, "
                      "cheap - use for iterating) or 'final' (1344x768). 'seconds' "
                      "must be 4-15. 'takes' generates that many variations per "
                      "prompt. Pass image paths from add_reference_image as "
                      "reference_images, with mode 'i2v' or 'r2v'.")
async def queue_video(prompts: list[str], seconds: int = 10, preset: str = "draft",
                      takes: int = 1, mode: str = "t2v",
                      reference_images: list[str] | None = None) -> dict[str, Any]:
    if not prompts:
        return {"error": "no prompts given"}
    r = await _call("POST", "/api/jobs", json={
        "prompts": "\n".join(prompts), "seconds": seconds, "preset": preset,
        "count": takes, "mode": mode, "ref_images": reference_images or [],
    })
    if _failed(r):
        return _clean(r)
    return {"queued": r["count"], "job_ids": r["created"],
            "note": "The GPU starts automatically. Use list_jobs to follow progress."}


@mcp.tool(description="Hand an image on this computer to H3 Studio for use as a "
                      "reference. Give an absolute path to an existing image file. "
                      "Returns a path to pass to queue_video as a reference_image.")
async def add_reference_image(image_path: str) -> dict[str, Any]:
    src = Path(image_path).expanduser()
    if not src.exists() or not src.is_file():
        return {"error": f"no such file: {src}"}
    if src.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
        return {"error": f"not an image file: {src.name}"}

    # Dropped into the watched inbox rather than POSTed: one code path owns taking
    # images in, so anything arriving this way behaves exactly like a manual drop.
    inbox = Path(_cfg.output.inbox)
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / src.name
        n = 2
        while dest.exists():
            dest = inbox / f"{src.stem}_{n}{src.suffix}"
            n += 1
        shutil.copy2(src, dest)
    except OSError as e:
        return {"error": f"could not copy into the inbox: {e}"}
    return {
        "ok": True,
        "name": dest.name,
        "note": "H3 Studio picks this up within a couple of seconds and shows it "
                "as a reference image in the app.",
    }


@mcp.tool(description="List recent jobs and their state, including the output file "
                      "path for finished ones.")
async def list_jobs(limit: int = 20) -> dict[str, Any]:
    r = await _call("GET", "/api/jobs")
    if _failed(r):
        return _clean(r)
    jobs = [{
        "id": j["id"], "prompt": j["prompt"], "status": j["status"],
        "seconds": j["seconds"], "preset": j["preset"],
        "output": j.get("output_path"), "error": j.get("error"),
    } for j in r["jobs"][:limit]]
    return {"jobs": jobs}


@mcp.tool(description="Set how the GPU is managed: 'auto' starts it when work "
                      "arrives and stops it when idle (default), 'keep-warm' holds "
                      "it up, 'off' shuts it down and pauses the queue.")
async def set_gpu_policy(policy: str) -> dict[str, Any]:
    if policy not in {"auto", "keep-warm", "off"}:
        return {"error": "policy must be auto, keep-warm or off"}
    return await _call("POST", "/api/policy", json={"policy": policy})


def main() -> None:
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
