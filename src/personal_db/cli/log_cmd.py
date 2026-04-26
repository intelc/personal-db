from typing import Annotated

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.log_event import log_event


def log(
    tracker: Annotated[str, typer.Argument()],
    fields: Annotated[list[str], typer.Argument(help="key=value pairs")],
) -> None:
    """Log a manual event to a tracker."""
    cfg = Config(root=get_root())
    parsed = {}
    for pair in fields:
        if "=" not in pair:
            typer.echo(f"bad field: {pair!r} (expected key=value)", err=True)
            raise typer.Exit(2)
        k, v = pair.split("=", 1)
        parsed[k] = v
    rowid = log_event(cfg, tracker, parsed)
    typer.echo(f"logged {tracker} rowid={rowid}")
