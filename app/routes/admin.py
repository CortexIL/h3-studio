"""Admin API. Filled in by the next commit; the router exists so main.py wires it."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])
