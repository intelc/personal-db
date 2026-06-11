from __future__ import annotations

import json
import sys

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.remote_sources.spark import SparkCommandError, SparkEmailSource
from personal_db.sources import (
    discover_sources,
    install_source_template,
    list_bundled_sources,
    update_source_template,
)

app = typer.Typer(no_args_is_help=True, help="Remote/live source commands")

spark_app = typer.Typer(no_args_is_help=True, help="Spark email remote source")
SPARK_FOLDER_ARG = typer.Argument(None, help="Optional Spark folder identifiers")


def _emit(result, *, raw: bool) -> None:
    if raw:
        sys.stdout.write(result.raw_text)
        if result.raw_text and not result.raw_text.endswith("\n"):
            sys.stdout.write("\n")
        return
    json.dump(result.as_dict(), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _source() -> SparkEmailSource:
    return SparkEmailSource.from_config(Config(root=get_root()))


def _run(fn, *, raw: bool = False) -> None:
    try:
        _emit(fn(), raw=raw)
    except SparkCommandError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e


@app.command("list")
def list_sources() -> None:
    """List installed remote sources."""
    cfg = Config(root=get_root())
    sources = discover_sources(cfg)
    if not sources:
        typer.echo(
            "No remote sources installed. Use `personal-db source available` "
            "or `personal-db source install <builtin>`."
        )
        return
    for definition in sources.values():
        status = "enabled" if definition.manifest.enabled else "disabled"
        typer.echo(
            f"  {definition.name:20s} {definition.source:10s} "
            f"{status:8s} {definition.manifest.description}"
        )


@app.command("available")
def available() -> None:
    """List bundled remote source templates available to install."""
    bundled = list_bundled_sources()
    if not bundled:
        typer.echo("No bundled remote sources available.")
        return
    for name in bundled:
        typer.echo(f"  {name}")


@app.command("install")
def install(name: str) -> None:
    """Copy a bundled remote source definition into the user's sources/ directory."""
    cfg = Config(root=get_root())
    try:
        dest = install_source_template(cfg, name)
    except (FileExistsError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(f"Installed source {name} -> {dest}")


@app.command("reinstall")
def reinstall(name: str) -> None:
    """Overwrite canonical installed source files from the bundled template."""
    cfg = Config(root=get_root())
    try:
        dest = update_source_template(cfg, name)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(f"Reinstalled source {name} -> {dest}")


@spark_app.command("check")
def spark_check(raw: bool = typer.Option(False, "--raw", help="Print Spark output only")) -> None:
    """Check whether Spark CLI is available."""
    _run(lambda: _source().check(), raw=raw)


@spark_app.command("accounts")
def spark_accounts(
    raw: bool = typer.Option(False, "--raw", help="Print Spark output only"),
) -> None:
    """List Spark email accounts."""
    _run(lambda: _source().accounts(), raw=raw)


@spark_app.command("folders")
def spark_folders(
    scope: str | None = typer.Argument(None, help="Optional account/team/folder scope"),
    raw: bool = typer.Option(False, "--raw", help="Print Spark output only"),
) -> None:
    """List Spark folders and message counts."""
    _run(lambda: _source().folders(scope), raw=raw)


@spark_app.command("emails")
def spark_emails(
    folder: list[str] = SPARK_FOLDER_ARG,
    filter_: str | None = typer.Option(None, "--filter", help="Spark/Gmail-style filter"),
    page: int = typer.Option(1, "--page", help="1-based page number"),
    page_size: int = typer.Option(50, "--page-size", help="Emails per page"),
    order: str | None = typer.Option(None, "--order", help="ascending or descending"),
    new_senders: bool = typer.Option(False, "--new-senders", help="Only new sender emails"),
    raw: bool = typer.Option(False, "--raw", help="Print Spark output only"),
) -> None:
    """List Spark emails with optional filters."""
    _run(
        lambda: _source().emails(
            folders=folder,
            filter_=filter_,
            page=page,
            page_size=page_size,
            order=order,
            new_senders=new_senders,
        ),
        raw=raw,
    )


@spark_app.command("search")
def spark_search(
    about: str = typer.Argument(..., help="Search topic"),
    filter_: str | None = typer.Option(None, "--filter", help="Spark/Gmail-style filter"),
    in_: str | None = typer.Option(None, "--in", help="Account/team/folder scope"),
    raw: bool = typer.Option(False, "--raw", help="Print Spark output only"),
) -> None:
    """Search Spark emails by topic."""
    _run(lambda: _source().search(about, filter_=filter_, in_=in_), raw=raw)


@spark_app.command("thread")
def spark_thread(
    message_id: str = typer.Argument(..., help="Spark message ID"),
    download_attachments: bool = typer.Option(
        False, "--download-attachments", help="Ask Spark to download attachments"
    ),
    raw: bool = typer.Option(False, "--raw", help="Print Spark output only"),
) -> None:
    """Read a full Spark email thread."""
    _run(
        lambda: _source().thread(message_id, download_attachments=download_attachments),
        raw=raw,
    )


app.add_typer(spark_app, name="spark")
