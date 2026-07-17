from __future__ import annotations

import json
import sys

import typer

from personal_db.cli.state import get_root
from personal_db.core.config import Config
from personal_db.context_providers.email import SparkEmailContextProvider
from personal_db.remote_sources.spark import SparkCommandError, SparkSourceConfigError

app = typer.Typer(no_args_is_help=True, help="Semantic context provider commands")
email_app = typer.Typer(no_args_is_help=True, help="Email context provider")


def _emit(result, *, raw: bool) -> None:
    if raw:
        sys.stdout.write(result.raw_text)
        if result.raw_text and not result.raw_text.endswith("\n"):
            sys.stdout.write("\n")
        return
    json.dump(result.as_dict(), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _email_provider() -> SparkEmailContextProvider:
    return SparkEmailContextProvider.from_config(Config(root=get_root()))


def _run(fn, *, raw: bool = False) -> None:
    try:
        _emit(fn(), raw=raw)
    except (SparkCommandError, SparkSourceConfigError, ValueError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e


@email_app.command("search-receipts")
def search_receipts(
    merchant: str | None = typer.Option(None, "--merchant", help="Merchant/vendor hint"),
    amount: str | None = typer.Option(None, "--amount", help="Transaction amount hint"),
    date_: str | None = typer.Option(
        None,
        "--date",
        help="Transaction date as YYYY-MM-DD",
    ),
    window_days: int = typer.Option(7, "--window-days", help="Days around date to search"),
    scope: str | None = typer.Option(None, "--in", help="Optional Spark account/folder scope"),
    raw: bool = typer.Option(False, "--raw", help="Print provider raw text only"),
) -> None:
    """Find receipt-like email candidates for a transaction."""
    _run(
        lambda: _email_provider().search_receipts(
            merchant=merchant,
            amount=amount,
            date_=date_,
            window_days=window_days,
            scope=scope,
        ),
        raw=raw,
    )


@email_app.command("thread")
def read_thread(
    message_id: str = typer.Argument(..., help="Spark message ID"),
    download_attachments: bool = typer.Option(
        False,
        "--download-attachments",
        help="Ask Spark to download attachments",
    ),
    raw: bool = typer.Option(False, "--raw", help="Print provider raw text only"),
) -> None:
    """Read a Spark email thread as context evidence."""
    _run(
        lambda: _email_provider().read_thread(
            message_id,
            download_attachments=download_attachments,
        ),
        raw=raw,
    )


app.add_typer(email_app, name="email")
