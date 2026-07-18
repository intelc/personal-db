"""`personal-db app` — manage local app surfaces."""

from __future__ import annotations

import typer

from personal_db.cli.state import get_root
from personal_db.core.apps import (
    apply_app_schema,
    discover_apps,
    install_app_template,
    list_bundled_apps,
    update_app_template,
)
from personal_db.core.config import Config
from personal_db.core.pack_deps import DepsInstallError, app_python_deps, install_app_deps


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


def deps(
    name: str = typer.Argument(None),
    all_: bool = typer.Option(
        False, "--all", help="Install declared python_deps for every installed app"
    ),
) -> None:
    """Install an app's declared python_deps into <root>/lib/ (mirrors `tracker deps`)."""
    if name is not None and all_:
        typer.echo("specify either an app name or --all, not both", err=True)
        raise typer.Exit(2)
    if name is None and not all_:
        typer.echo("specify an app name or --all", err=True)
        raise typer.Exit(2)

    cfg = Config(root=get_root())
    if all_:
        apps_dir = cfg.apps_dir
        if not apps_dir.exists():
            typer.echo("no apps installed")
            return
        names = sorted(
            d.name for d in apps_dir.iterdir() if d.is_dir() and (d / "app.yaml").is_file()
        )
    else:
        names = [name]

    exit_code = 0
    for n in names:
        try:
            declared = app_python_deps(cfg, n)
        except FileNotFoundError as e:
            typer.echo(f"{n}: {e}", err=True)
            exit_code = 1
            continue
        if not declared:
            typer.echo(f"{n}: no python_deps declared")
            continue
        typer.echo(f"{n}: installing {', '.join(declared)} -> {cfg.lib_dir}")
        try:
            result = install_app_deps(cfg, n)
        except DepsInstallError as e:
            typer.echo(f"{n}: {e}", err=True)
            exit_code = 1
            continue
        typer.echo(f"{n}: {result.detail}")
    if exit_code:
        raise typer.Exit(exit_code)
