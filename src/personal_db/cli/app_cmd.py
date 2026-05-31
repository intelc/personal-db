"""`personal-db app` — manage local app surfaces."""

from __future__ import annotations

import typer

from personal_db.apps import (
    apply_app_schema,
    discover_apps,
    install_app_template,
    list_bundled_apps,
    update_app_template,
)
from personal_db.cli.state import get_root
from personal_db.config import Config


def list_cmd() -> None:
    """List discovered apps and their source."""
    cfg = Config(root=get_root())
    apps = discover_apps(cfg)
    if not apps:
        typer.echo(
            "No apps discovered. Use `personal-db app install <builtin>` "
            "or add an app under <root>/apps."
        )
        return
    for app in apps.values():
        typer.echo(f"  {app.name:20s} {app.source:10s} {app.manifest.description}")


def available() -> None:
    """List bundled app templates available to install."""
    bundled = list_bundled_apps()
    if not bundled:
        typer.echo("No bundled apps available.")
        return
    for name in bundled:
        typer.echo(f"  {name}")


def install(name: str) -> None:
    """Copy a bundled app template into the user's apps/ directory."""
    cfg = Config(root=get_root())
    try:
        dest = install_app_template(cfg, name)
    except (FileExistsError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    apply_app_schema(cfg, dest)
    typer.echo(f"Installed app {name} -> {dest}")


def reinstall(name: str) -> None:
    """Overwrite canonical installed app files from the bundled template."""
    cfg = Config(root=get_root())
    try:
        dest = update_app_template(cfg, name)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    apply_app_schema(cfg, dest)
    typer.echo(f"Reinstalled app {name} -> {dest}")
