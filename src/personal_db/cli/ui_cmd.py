"""`personal-db ui` — launch the menubar shell. The dashboard is served by the daemon."""

from __future__ import annotations

import typer

from personal_db.cli.state import get_root
from personal_db.core.config import Config


def ui(
    port: int = typer.Option(8765, "--port", help="Daemon dashboard port (for the 'Open dashboard' menu item)"),
) -> None:
    """Launch the menubar shell. The dashboard runs in the daemon at
    http://127.0.0.1:<port>/ — make sure `personal-db daemon install` was run."""
    from personal_db.services.ui.menubar import run_menubar

    cfg = Config(root=get_root())
    run_menubar(cfg, port=port)
