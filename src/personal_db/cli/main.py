from pathlib import Path

import typer

from personal_db.cli import init_cmd
from personal_db.cli.state import _state, get_root  # noqa: F401 — re-exported for callers

app = typer.Typer(no_args_is_help=True, help="Personal data layer CLI")


@app.callback()
def _global(root: str = typer.Option(None, "--root", help="Override data root")):
    if root:
        _state["root"] = Path(root).expanduser()


app.command("init")(init_cmd.run)

if __name__ == "__main__":
    app()
