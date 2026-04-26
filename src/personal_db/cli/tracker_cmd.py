import shutil
from importlib import resources

import typer

from personal_db.cli.state import get_root
from personal_db.manifest import load_manifest

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
    root = get_root()
    dest = root / "trackers" / name
    if dest.exists():
        typer.echo(f"already installed: {dest}", err=True)
        raise typer.Exit(1)
    src_pkg = resources.files("personal_db.templates.trackers").joinpath(name)
    if not src_pkg.is_dir():
        typer.echo(f"unknown built-in tracker: {name}", err=True)
        raise typer.Exit(1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(src_pkg) as src_path:
        shutil.copytree(src_path, dest)
    typer.echo(f"Installed {name} -> {dest}")
