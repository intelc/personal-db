import typer

from personal_db import scheduler
from personal_db.cli.state import get_root


def install(interval_seconds: int = typer.Option(600, "--interval-seconds")) -> None:
    """Write a launchd plist and load it. Runs `personal-db sync --due` every interval."""
    p = scheduler.install(get_root(), interval_seconds)
    typer.echo(f"installed: {p}")


def uninstall() -> None:
    scheduler.uninstall()
    typer.echo("uninstalled")


def status() -> None:
    typer.echo(scheduler.status())
