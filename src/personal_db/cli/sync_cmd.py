import typer

from personal_db.services.daemon import client as dc

_DAEMON_HINT = "personal-db daemon not running. Run `personal-db daemon install`"


def sync(
    name: str = typer.Argument(None),
    due: bool = typer.Option(False, "--due", help="Run only trackers that are due"),
) -> None:
    """Run sync for a tracker, or all due trackers (delegates to daemon)."""
    try:
        if due:
            out = dc.sync_due()
            for n, status in out.get("results", {}).items():
                typer.echo(f"  {n}: {status}")
        elif name:
            dc.sync_one(name)
            typer.echo(f"synced {name}")
        else:
            typer.echo("specify a tracker name or --due", err=True)
            raise typer.Exit(2)
    except dc.DaemonUnreachable:
        typer.echo(_DAEMON_HINT, err=True)
        raise typer.Exit(2) from None
    except dc.DaemonError as e:
        typer.echo(f"daemon error: {e}", err=True)
        raise typer.Exit(1) from None


def backfill(
    name: str = typer.Argument(...),
    from_: str = typer.Option(None, "--from"),
    to: str = typer.Option(None, "--to"),
) -> None:
    """Backfill a tracker over a date range (delegates to daemon)."""
    try:
        dc.backfill(name, from_, to)
        typer.echo(f"backfilled {name}")
    except dc.DaemonUnreachable:
        typer.echo(_DAEMON_HINT, err=True)
        raise typer.Exit(2) from None
    except dc.DaemonError as e:
        typer.echo(f"daemon error: {e}", err=True)
        raise typer.Exit(1) from None
