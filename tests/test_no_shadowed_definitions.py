"""No module may define the same name twice.

A second `def` of a name silently replaces the first, and Python says nothing.
A new `counts_for` written near the top of app/store/jobs.py was shadowed by the
one already near the bottom, so every caller quietly got the wrong function and
the whole studio feed broke on a KeyError. This is the cheapest possible guard,
and unlike the tests that would have caught it, it needs no database.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted(p for p in (ROOT / "app").rglob("*.py"))


def _top_level_names(tree: ast.Module) -> list[str]:
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(ROOT)))
def test_each_module_defines_every_name_once(path: Path) -> None:
    names = _top_level_names(ast.parse(path.read_text(encoding="utf-8")))
    twice = sorted({n for n in names if names.count(n) > 1})
    assert not twice, f"{path.relative_to(ROOT)} defines {twice} more than once"
