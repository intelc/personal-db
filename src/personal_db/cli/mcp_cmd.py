"""`personal-db mcp` — MCP stdio server (default), plus install subcommand."""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
from pathlib import Path

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.mcp_server.server import run as run_server
from personal_db.wizard.mcp_setup import _TARGETS, run_mcp_setup_menu

mcp_app = typer.Typer(
    no_args_is_help=False,
    invoke_without_command=True,
    help="MCP server + agent install commands",
)


@mcp_app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Run the MCP stdio server when invoked with no subcommand.

    Preserves the original `personal-db mcp` behavior — agents (Claude Code,
    Cursor, Claude Desktop) all spawn this command directly.
    """
    if ctx.invoked_subcommand is not None:
        return
    cfg = Config(root=get_root())
    asyncio.run(run_server(cfg))


_MCP_CMDLINE_RE = re.compile(r"\bpersonal-db\s+mcp\s*$")


def _parse_ps_output(text: str) -> list[tuple[int, int, str]]:
    """Parse `ps -ax -o pid=,ppid=,command=` output into (pid, ppid, cmd) rows."""
    out: list[tuple[int, int, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        out.append((pid, ppid, parts[2]))
    return out


def _find_mcp_processes() -> list[tuple[int, int, str]]:
    """Return processes whose cmdline ends in `personal-db mcp` (no subcommand)."""
    try:
        proc = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid=,command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [row for row in _parse_ps_output(proc.stdout) if _MCP_CMDLINE_RE.search(row[2])]


def _process_label(pid: int) -> str:
    """Short, human label for a process — recognizes the common host apps."""
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return f"pid={pid}"
    cmd = proc.stdout.strip()
    if not cmd:
        return f"pid={pid} (gone)"
    if "Claude.app" in cmd:
        return "Claude.app"
    if "Cursor" in cmd:
        return "Cursor"
    if "claude-code" in cmd or "claude code" in cmd.lower():
        return "Claude Code"
    head = cmd.split()[0]
    return Path(head).name if "/" in head else head


@mcp_app.command("refresh")
def refresh() -> None:
    """SIGTERM running personal-db MCP subprocesses so hosts respawn fresh ones.

    Long-running MCP subprocesses (Claude desktop, Cursor, Claude Code) hold the
    pre-edit personal_db modules in memory. Run this after editing src/personal_db/
    to force the host apps to respawn fresh subprocesses on their next tool call.
    """
    me = os.getpid()
    procs = _find_mcp_processes()
    if not procs:
        typer.echo("no personal-db mcp processes running")
        return
    killed: list[tuple[int, str]] = []
    skipped: list[tuple[int, str]] = []
    for pid, ppid, _cmd in procs:
        if pid == me:
            skipped.append((pid, "self"))
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append((pid, _process_label(ppid)))
        except ProcessLookupError:
            pass
        except PermissionError as e:
            skipped.append((pid, f"permission: {e}"))
    for pid, parent in killed:
        typer.echo(f"killed  {pid:>6}  parent={parent}")
    for pid, reason in skipped:
        typer.echo(f"skipped {pid:>6}  ({reason})")
    typer.echo(f"\n{len(killed)} killed, {len(skipped)} skipped")


@mcp_app.command("install")
def install(
    target: str = typer.Argument(
        None,
        help="claude_code | cursor | claude_desktop. Omit to pick from a menu.",
    ),
) -> None:
    """Install the personal_db MCP server into an agent's config.

    Auto-edits the relevant JSON file (Claude Desktop, Cursor) or shells out to
    `claude mcp add` (Claude Code). With no target argument, opens an interactive
    menu of all detected targets.
    """
    cfg = Config(root=get_root())
    if target is None:
        run_mcp_setup_menu(cfg)
        return
    if target not in _TARGETS:
        typer.echo(
            f"unknown target: {target}\nvalid: {', '.join(_TARGETS)}",
            err=True,
        )
        raise typer.Exit(1)
    ok, detail = _TARGETS[target].auto()
    icon = "✓" if ok else "✗"
    typer.echo(f"{icon} {detail}")
    if not ok:
        raise typer.Exit(1)
