"""`personal-db mcp` — MCP stdio server (default), plus install subcommand."""

from __future__ import annotations

import asyncio

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
