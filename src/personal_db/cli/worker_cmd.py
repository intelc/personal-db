from __future__ import annotations

import json
import os
import sys

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.worker import install as wi
from personal_db.worker.enrich import FINANCE_RECEIPT_V1, SUPPORTED_KINDS, run_loop


def enrich(
    kind: str = typer.Option(FINANCE_RECEIPT_V1, "--kind", help="Worker kind to run"),
    batch_size: int = typer.Option(1, "--batch-size", help="Jobs to run per tick"),
    interval_seconds: float = typer.Option(900, "--interval-seconds", help="Sleep between ticks"),
    lease_seconds: int = typer.Option(1200, "--lease-seconds", help="Queue lease duration"),
    once: bool = typer.Option(False, "--once", help="Run one tick and exit"),
) -> None:
    """Run an enrichment worker in the foreground."""
    if kind not in SUPPORTED_KINDS:
        raise typer.BadParameter(f"unsupported worker kind: {kind}")
    cfg = Config(root=get_root())
    run_loop(
        cfg,
        kind=kind,
        batch_size=batch_size,
        interval_seconds=interval_seconds,
        lease_seconds=lease_seconds,
        once=once,
    )


def install(
    kind: str = typer.Option(FINANCE_RECEIPT_V1, "--kind", help="Worker kind to install"),
    batch_size: int = typer.Option(1, "--batch-size", help="Jobs to run per tick"),
    interval_seconds: float = typer.Option(900, "--interval-seconds", help="Sleep between ticks"),
    lease_seconds: int = typer.Option(1200, "--lease-seconds", help="Queue lease duration"),
) -> None:
    """Install the launchd plist for the enrichment worker and load it."""
    if kind not in SUPPORTED_KINDS:
        raise typer.BadParameter(f"unsupported worker kind: {kind}")
    if _env_enabled("PERSONAL_DB_FINANCE_RECEIPT_V1_WORKER_ENABLED"):
        typer.echo(
            "note: PERSONAL_DB_FINANCE_RECEIPT_V1_WORKER_ENABLED is ignored by the daemon; "
            "this worker LaunchAgent is the v1 execution path."
        )
    result = wi.install(
        get_root(),
        kind=kind,
        batch_size=batch_size,
        interval_seconds=interval_seconds,
        lease_seconds=lease_seconds,
    )
    typer.echo(f"installed: {result['plist']}")


def uninstall() -> None:
    """Unload and remove the enrichment worker plist."""
    wi.uninstall()
    typer.echo("uninstalled")


def status() -> None:
    """Print launchctl's view of the enrichment worker."""
    typer.echo(wi.status())


def info() -> None:
    """Print structured enrichment worker status."""
    json.dump(wi.info(get_root()), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def log(lines: int = typer.Option(50, "--tail", help="Number of log lines to show")) -> None:
    """Print the tail of the enrichment worker log."""
    result = wi.log_tail(get_root(), lines=lines)
    if not result["exists"]:
        typer.echo(f"log not found: {result['path']}")
        return
    for line in result["lines"]:
        typer.echo(line)


def restart(
    kind: str = typer.Option(FINANCE_RECEIPT_V1, "--kind", help="Worker kind to install"),
    batch_size: int = typer.Option(1, "--batch-size", help="Jobs to run per tick"),
    interval_seconds: float = typer.Option(900, "--interval-seconds", help="Sleep between ticks"),
    lease_seconds: int = typer.Option(1200, "--lease-seconds", help="Queue lease duration"),
) -> None:
    """Reinstall the worker plist (unload + load)."""
    if kind not in SUPPORTED_KINDS:
        raise typer.BadParameter(f"unsupported worker kind: {kind}")
    if _env_enabled("PERSONAL_DB_FINANCE_RECEIPT_V1_WORKER_ENABLED"):
        typer.echo(
            "note: PERSONAL_DB_FINANCE_RECEIPT_V1_WORKER_ENABLED is ignored by the daemon; "
            "this worker LaunchAgent is the v1 execution path."
        )
    wi.uninstall()
    result = wi.install(
        get_root(),
        kind=kind,
        batch_size=batch_size,
        interval_seconds=interval_seconds,
        lease_seconds=lease_seconds,
    )
    typer.echo(f"restarted: {result['plist']}")


def _env_enabled(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}
