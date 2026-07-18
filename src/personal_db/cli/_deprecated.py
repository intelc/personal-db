"""Backward-compat aliases for commands moved under `personal-db dev` (2d).

Phase 2d pushes developer plumbing (`query`, `enrich`, `source`, `context`,
`permission`, `code-agent-hook-write`, `mcp refresh`, `daemon run`,
`tracker new`) under `personal-db dev ...` so top-level `--help` only shows
what a non-developer user needs. The OLD top-level invocations keep working
(hidden from `--help`, not removed) so existing scripts/muscle-memory don't
break; they print a one-line pointer at the new location first.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import typer

_NOTE = "(moved to `personal-db {new_path}` -- this alias still works but is deprecated)"


def leaf_alias(fn: Callable[..., Any], new_path: str) -> Callable[..., Any]:
    """Wrap a single-command callback: print the deprecation note, then run
    `fn` unchanged. `functools.wraps` preserves `fn`'s signature (via
    `__wrapped__`, which `inspect.signature` follows) so Typer still parses
    the same CLI arguments/options as the un-wrapped command registered at
    the new location."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        typer.echo(_NOTE.format(new_path=new_path), err=True)
        return fn(*args, **kwargs)

    return wrapper


def legacy_group_note(new_path: str) -> Callable[[typer.Context], None]:
    """Build a Typer *group* callback for a sub-app that's mounted at both
    its old top-level name and its new `dev` location (the same Typer
    object, added twice). Prints the deprecation note only when reached via
    the old path -- `ctx.command_path` differs by mount point even though
    it's the same underlying Typer app, so this is enough to tell them
    apart without maintaining two copies of the sub-app."""

    def _callback(ctx: typer.Context) -> None:
        if "dev" not in ctx.command_path.split():
            typer.echo(_NOTE.format(new_path=new_path), err=True)

    return _callback
