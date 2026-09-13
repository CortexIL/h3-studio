"""The prompt helper: the owner's short description, rewritten the way MiniMax's
own pipeline (H3-Context-IR) writes prompts for the model.

MiniMax publishes the format its rewriter produces - a timeline of shots with a
fixed camera vocabulary, then `overall_soundscape` and `non_diegetic_music` -
and calls that step "critical to the quality of the final output". The guides
in this folder are copied verbatim from the model's Hugging Face repository
(docs/VIDEO_PROMPT_WRITING_GUIDE_*.md, MiniMax H3 Community License). They are
handed to a language model as instructions; nothing here calls the video model.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-sonnet-5"


class PromptHelperError(Exception):
    """The helper could not produce a rewrite; the message is safe to show."""


def guide(mode: str) -> str:
    name = "guide_ref_en.md" if mode == "r2v" else "guide_base_en.md"
    return (HERE / name).read_text(encoding="utf-8")


def _task_name(mode: str, has_start: bool, has_end: bool) -> str:
    if mode == "t2v":
        return "T2VA (no image)"
    if mode == "flf2v":
        return "FL2VA (a first frame <Picture 1> and a last frame <Picture 2>)"
    if mode == "extend":
        return ("continuation of an existing clip: its last moment is anchored at 0.00 s "
                "(describe the action carrying on from there)"
                + ("; the clip must end on <Picture 1> (L2VA)" if has_end else ""))
    if mode == "r2v":
        return "full-reference mode (Ref2VA)"
    return "I2VA (<Picture 1> is the first frame)" if has_start else "T2VA (no image)"


def system_prompt(mode: str) -> str:
    return (
        "You rewrite a video-maker's short description into the exact prompt format "
        "MiniMax H3 expects. Follow the guide below precisely: its shot labels, its camera "
        "vocabulary (motion type + amplitude + speed), its speaker IDs, its two sound fields.\n\n"
        "Rules:\n"
        "- Keep every fact the person gave: who, what, where, the order of events, on-screen "
        "text in quotes verbatim, dialogue verbatim in its original language. Add only the "
        "visual and audio detail the format needs; invent no new story beats.\n"
        "- Write in English, except quoted on-screen text and dialogue.\n"
        "- Do NOT write the alignment instruction line (the studio adds it) and do NOT write "
        "the field labels. Return only a JSON object with three string fields:\n"
        '  {"description": "[Shot 1] ... [Shot 2] At 00:0S.SSS, the camera cuts to ...", '
        '"sounds": "...", "music": "..."}\n'
        "- description: the integrated_multimodal_description body, starting with [Shot 1]. "
        "Use several shots only if the person described cuts or asked for them; a single "
        "shot with camera motion is the default.\n"
        "- sounds: the overall_soundscape text (1-4 sentences), or an empty string if the "
        "person asked for silence.\n"
        "- music: the non_diegetic_music text (1-3 sentences), or an empty string for no music.\n"
        "- Total length: keep the description under 1200 characters.\n\n"
        "THE GUIDE:\n\n" + guide(mode)
    )


def user_prompt(body: dict[str, Any]) -> str:
    facts = {
        "task": _task_name(str(body.get("mode") or "i2v"), bool(body.get("has_start")), bool(body.get("has_end"))),
        "duration_seconds": body.get("seconds"),
        "description_from_the_person": body.get("prompt") or "",
        "sounds_from_the_person": body.get("sound") or "",
        "music_from_the_person": body.get("music") or "",
        "keyframes_pinned_inside_the_clip": body.get("keyframes") or 0,
    }
    return json.dumps(facts, ensure_ascii=False, indent=2)


async def ask(api_key: str, system: str, user: str, *, max_tokens: int = 1500) -> str:
    """One call to the language model. Kept separate so tests can replace it."""
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION,
               "content-type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(ANTHROPIC_URL, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise PromptHelperError(f"could not reach the prompt helper: {e.__class__.__name__}")
    if r.status_code == 401:
        raise PromptHelperError("the prompt helper key was rejected")
    if r.status_code >= 400:
        raise PromptHelperError(f"the prompt helper answered {r.status_code}")
    data = r.json()
    return "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")


def parse(answer: str) -> dict[str, str]:
    """The three fields out of the model's answer, tolerant of a code fence around the JSON."""
    text = answer.strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise PromptHelperError("the prompt helper did not answer in the expected shape")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise PromptHelperError("the prompt helper did not answer in the expected shape")
    out = {k: str(obj.get(k) or "").strip() for k in ("description", "sounds", "music")}
    if not out["description"]:
        raise PromptHelperError("the prompt helper returned an empty description")
    return out


async def improve(api_key: str, body: dict[str, Any]) -> dict[str, str]:
    mode = str(body.get("mode") or "i2v")
    answer = await ask(api_key, system_prompt(mode), user_prompt(body))
    return parse(answer)


async def verify_key(api_key: str) -> None:
    """Fail loudly on a key the API refuses, before it is stored."""
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get("https://api.anthropic.com/v1/models?limit=1", headers=headers)
    except httpx.HTTPError as e:
        raise PromptHelperError(f"could not reach the prompt helper to verify the key: {e.__class__.__name__}")
    if r.status_code in (401, 403):
        raise PromptHelperError("the prompt helper rejected that key")
    if r.status_code >= 400:
        raise PromptHelperError(f"the prompt helper answered {r.status_code} for that key")
