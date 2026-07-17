"""`personal-db daemon` — manage the long-running sync daemon."""

from __future__ import annotations

import typer

from personal_db.cli.state import get_root
from personal_db.core.config import Config
from personal_db.services.daemon import install as di


def install() -> None:
    """Install the launchd plist for the daemon and load it.

    Auto-migrates the old `com.personal_db.scheduler.plist` if present.
    """
    result = di.install(get_root())
    if result["migrated_old_scheduler"]:
        typer.echo("note: removed old com.personal_db.scheduler.plist")
    typer.echo(f"installed: {result['plist']}")


def uninstall() -> None:
    """Unload and remove the daemon plist."""
    di.uninstall()
    typer.echo("uninstalled")


def status() -> None:
    """Print launchctl's view of the daemon."""
    typer.echo(di.status())


def restart() -> None:
    """Reinstall the plist (unload + load). Equivalent to `uninstall && install`."""
    di.uninstall()
    result = di.install(get_root())
    if result["migrated_old_scheduler"]:
        typer.echo("note: removed old com.personal_db.scheduler.plist")
    typer.echo(f"restarted: {result['plist']}")


def run(
    port: int = typer.Option(8765, "--port"),
    interval_seconds: float = typer.Option(600, "--interval-seconds"),
) -> None:
    """Run the daemon in the foreground (called by launchd)."""
    from personal_db.services.daemon.server import run as _run

    cfg = Config(root=get_root())
    _run(cfg, port=port, interval_seconds=interval_seconds)
