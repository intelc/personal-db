from pathlib import Path

import typer
from dotenv import load_dotenv

from personal_db.cli import (
    init_cmd,
    log_cmd,
    mcp_cmd,
    permission_cmd,
    scheduler_cmd,
    setup_cmd,
    sync_cmd,
    tracker_cmd,
    ui_cmd,
)
from personal_db.cli.state import _state, get_root

app = typer.Typer(no_args_is_help=True, help="Personal data layer CLI")


def _load_root_env(root: Path) -> None:
    """Load <root>/.env if present. override=False so shell env wins."""
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


@app.callback()
def _global(root: str = typer.Option(None, "--root", help="Override data root")):
    if root:
        _state["root"] = Path(root).expanduser()
    _load_root_env(get_root())


app.command("init")(init_cmd.run)
app.command("setup")(setup_cmd.run)
app.add_typer(mcp_cmd.mcp_app, name="mcp")
app.command("sync")(sync_cmd.sync)
app.command("backfill")(sync_cmd.backfill)
app.command("log")(log_cmd.log)
app.command("ui")(ui_cmd.ui)

tracker_app = typer.Typer(no_args_is_help=True, help="Tracker management")
tracker_app.command("new")(tracker_cmd.new)
tracker_app.command("list")(tracker_cmd.list_cmd)
tracker_app.command("install")(tracker_cmd.install)
tracker_app.command("setup")(tracker_cmd.setup)
app.add_typer(tracker_app, name="tracker")

permission_app = typer.Typer(no_args_is_help=True, help="OS permission helpers")
permission_app.command("check")(permission_cmd.check)
app.add_typer(permission_app, name="permission")

sched_app = typer.Typer(no_args_is_help=True, help="Background scheduler")
sched_app.command("install")(scheduler_cmd.install)
sched_app.command("uninstall")(scheduler_cmd.uninstall)
sched_app.command("status")(scheduler_cmd.status)
app.add_typer(sched_app, name="scheduler")

if __name__ == "__main__":
    app()
