"""`personal-db ui` — launch the menu bar app + dashboard server."""

from __future__ import annotations

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config


def ui(
    port: int = typer.Option(8765, "--port", help="Dashboard port"),
    no_menubar: bool = typer.Option(
        False, "--no-menubar", help="Run only the dashboard server, no menu bar (useful in tmux)"
    ),
) -> None:
    """Launch the personal_db menu bar app + dashboard.

    The menu bar lives in the macOS status bar (rumps); the dashboard runs at
    http://127.0.0.1:<port>/. Use --no-menubar to skip rumps and serve only
    the dashboard (e.g. when running headless or over SSH).
    """
    cfg = Config(root=get_root())
    if no_menubar:
        from personal_db.ui.menubar import _start_server

        typer.echo(f"dashboard at http://127.0.0.1:{port}/  (Ctrl+C to stop)")
        _start_server(cfg, port)
    else:
        from personal_db.ui.menubar import run_menubar

        run_menubar(cfg, port=port)
