"""The prompt helper: rewrite a description into the model's own prompt format."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import prompting
from ..auth import current_user

router = APIRouter(prefix="/api/prompt", tags=["prompt"])


class ImproveBody(BaseModel):
    prompt: str
    mode: str | None = None
    seconds: int | None = None
    sound: str | None = None
    music: str | None = None
    has_start: bool = False
    has_end: bool = False
    keyframes: int = 0


@router.post("/improve")
async def improve(body: ImproveBody, request: Request,
                  user: dict = Depends(current_user)) -> dict[str, Any]:
    """The description, the sounds and the music, rewritten the official way.

    409 when no key is set: the Admin page is where it goes, and the composer
    hides the button until then, so this is only reachable by hand.
    """
    key = request.app.state.cfg.anthropic_api_key
    if not key:
        raise HTTPException(409, "the prompt helper is not set up; an admin adds its key on the Admin page")
    if len(body.prompt.strip()) < 3:
        raise HTTPException(400, "write a description first")
    try:
        return await prompting.improve(key, body.model_dump())
    except prompting.PromptHelperError as e:
        raise HTTPException(502, str(e))
