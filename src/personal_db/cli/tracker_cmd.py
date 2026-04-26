import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.db import apply_tracker_schema, init_db
from personal_db.installer import install_template
from personal_db.manifest import load_manifest
from personal_db.wizard.menu import run_menu
from personal_db.wizard.runner import run_tracker

_SCAFFOLD_MANIFEST = """\
name: {name}
description: TODO describe what this tracker captures
permission_type: none
setup_steps: []
schedule:
  every: 1h
time_column: ts
granularity: event
schema:
  tables:
    {name}:
      columns:
        id:    {{type: TEXT,    semantic: "primary key"}}
        ts:    {{type: TEXT,    semantic: "ISO-8601 event time (UTC)"}}
        value: {{type: INTEGER, semantic: "the recorded value"}}
related_entities: []
"""

_SCAFFOLD_SCHEMA = """\
CREATE TABLE IF NOT EXISTS {name} (
  id    TEXT PRIMARY KEY,
  ts    TEXT NOT NULL,
  value INTEGER
);
"""

_SCAFFOLD_INGEST = """\
from personal_db.tracker import Tracker

def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    \"\"\"Historical import. Idempotent.\"\"\"
    pass

def sync(t: Tracker) -> None:
    \"\"\"Incremental sync from cursor. Idempotent.\"\"\"
    pass
"""


def new(name: str) -> None:
    """Scaffold a new tracker."""
    root = get_root()
    d = root / "trackers" / name
    if d.exists():
        typer.echo(f"already exists: {d}", err=True)
        raise typer.Exit(1)
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(_SCAFFOLD_MANIFEST.format(name=name))
    (d / "schema.sql").write_text(_SCAFFOLD_SCHEMA.format(name=name))
    (d / "ingest.py").write_text(_SCAFFOLD_INGEST)
    typer.echo(f"Created tracker at {d}")


def list_cmd() -> None:
    """List installed trackers and their last-sync state."""
    root = get_root()
    trackers_dir = root / "trackers"
    if not trackers_dir.exists() or not any(trackers_dir.iterdir()):
        typer.echo(
            "No trackers installed. Use `personal-db tracker new <name>` or"
            " `personal-db tracker install <builtin>`."
        )
        return
    for d in sorted(trackers_dir.iterdir()):
        if d.is_dir() and (d / "manifest.yaml").exists():
            m = load_manifest(d / "manifest.yaml")
            typer.echo(f"  {m.name:20s} {m.permission_type:18s} {m.description}")


def install(name: str) -> None:
    """Copy a bundled tracker template into the user's trackers/ directory."""
    cfg = Config(root=get_root())
    try:
        dest = install_template(cfg, name)
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    # Apply schema eagerly so manual-capture trackers (life_context, habits)
    # have their tables ready without needing a no-op sync first.
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (dest / "schema.sql").read_text())
    typer.echo(f"Installed {name} -> {dest}")


def setup(name: str | None = typer.Argument(None)) -> None:
    """Configure a tracker's required env vars / OAuth / FDA / instructions, then test sync.

    No argument → opens an interactive menu of all installed trackers.
    Argument     → runs setup for that one tracker and exits.
    """
    cfg = Config(root=get_root())
    if name is None:
        run_menu(cfg)
    else:
        result = run_tracker(cfg, name)
        if not result.success:
            raise typer.Exit(1)
