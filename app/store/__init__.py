"""Persistence. One module per table, plus the pool that serves them all.

The pool accessor is `get_pool`, not `pool`: `pool` is the submodule, and a
re-export under the same name makes `from app.store import pool` hand back a
function where the caller expected a module.
"""
from __future__ import annotations

from .pool import (ORCHESTRATOR_LOCK_KEY, close_pool, connection, get_pool,
                   migrate, open_pool, try_advisory_lock)

__all__ = ["ORCHESTRATOR_LOCK_KEY", "close_pool", "connection", "get_pool",
           "migrate", "open_pool", "try_advisory_lock", "users"]

from . import users  # noqa: E402,F401  (after pool: users imports from it)
