import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.sync import backfill_one, sync_due, sync_one


def sync(
    name: str = typer.Argument(None),
    due: bool = typer.Option(False, "--due", help="Run only trackers that are due"),
) -> None:
    """Run sync for a tracker, or all due trackers."""
    cfg = Config(root=get_root())
    if due:
        results = sync_due(cfg)
        for n, status in results.items():
            typer.echo(f"  {n}: {status}")
    elif name:
        sync_one(cfg, name)
        typer.echo(f"synced {name}")
    else:
        typer.echo("specify a tracker name or --due", err=True)
        raise typer.Exit(2)


def backfill(
    name: str = typer.Argument(...),
    from_: str = typer.Option(None, "--from"),
    to: str = typer.Option(None, "--to"),
) -> None:
    """Backfill a tracker over a date range."""
    cfg = Config(root=get_root())
    backfill_one(cfg, name, from_, to)
    typer.echo(f"backfilled {name}")
