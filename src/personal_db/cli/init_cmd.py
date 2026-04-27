import typer
import yaml

from personal_db.cli.state import get_root
from personal_db.db import init_db


def run() -> None:
    """Initialize a personal_db root directory.

    The root is taken from the global `--root` option (see `personal-db --help`),
    falling back to `~/personal_db`.
    """
    root_p = get_root()
    for sub in ("trackers", "entities", "notes", "state", "state/oauth"):
        (root_p / sub).mkdir(parents=True, exist_ok=True)
    cfg = root_p / "config.yaml"
    if not cfg.exists():
        cfg.write_text(yaml.safe_dump({"root": str(root_p)}))
    for ename, default in (("people.yaml", "[]"), ("topics.yaml", "[]")):
        ep = root_p / "entities" / ename
        if not ep.exists():
            ep.write_text(default)
    init_db(root_p / "db.sqlite")
    typer.echo(f"Initialized {root_p}")
