from pathlib import Path

import typer

from personal_db.cli import init_cmd, log_cmd, permission_cmd, sync_cmd, tracker_cmd
from personal_db.cli.state import _state, get_root  # noqa: F401 — re-exported for callers

app = typer.Typer(no_args_is_help=True, help="Personal data layer CLI")


@app.callback()
def _global(root: str = typer.Option(None, "--root", help="Override data root")):
    if root:
        _state["root"] = Path(root).expanduser()


app.command("init")(init_cmd.run)
app.command("sync")(sync_cmd.sync)
app.command("backfill")(sync_cmd.backfill)
app.command("log")(log_cmd.log)

tracker_app = typer.Typer(no_args_is_help=True, help="Tracker management")
tracker_app.command("new")(tracker_cmd.new)
tracker_app.command("list")(tracker_cmd.list_cmd)
tracker_app.command("install")(tracker_cmd.install)
app.add_typer(tracker_app, name="tracker")

permission_app = typer.Typer(no_args_is_help=True, help="OS permission helpers")
permission_app.command("check")(permission_cmd.check)
app.add_typer(permission_app, name="permission")

if __name__ == "__main__":
    app()
