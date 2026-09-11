"""The polling endpoint, and liveness for the platform."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..auth import current_user

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def status(request: Request,
                 user: dict = Depends(current_user)) -> dict[str, Any]:
    orch = request.app.state.orch
    cfg = request.app.state.cfg
    snap = await orch.snapshot_for(user["id"])
    return {**snap, "config": cfg.public(),
            "user": {"email": user["email"], "role": user["role"]}}


# Deliberately outside the auth dependency: the platform's health check has no
# session, and an unauthenticated 401 would read as a dead container.
health_router = APIRouter(prefix="/api", tags=["health"])


@health_router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {"ok": True, "leader": getattr(request.app.state.orch, "leader", False)}
