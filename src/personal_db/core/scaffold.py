"""Scaffold a new (custom) tracker's canonical files into <root>/trackers/<name>/.

Shared by the CLI (`personal-db dev tracker new`, aliased as the hidden
`personal-db tracker new`) and the web setup wizard's "Add your own source"
flow (`GET/POST /setup/new`, `personal_db.services.daemon.routes.setup`).
Writes stay entirely inside `cfg.root` -- unlike `core.installer`, there is no
bundled-template source, no schema application, and no validation stamping
(custom trackers aren't pre-trusted the way bundled ones are; `tracker
validate` is how a hand-edited/agent-authored tracker earns sync's trust).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from personal_db.core.config import Config

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
from personal_db.core.tracker import Tracker

def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    \"\"\"Historical import. Idempotent.\"\"\"
    pass

def sync(t: Tracker) -> None:
    \"\"\"Incremental sync from cursor. Idempotent.\"\"\"
    pass
"""

# Key order the scaffolded manifest.yaml is written in -- used by
# apply_manifest_overrides() to keep `name`/`title`/`description` grouped at
# the top after a round-trip through yaml.safe_load/safe_dump (which would
# otherwise alphabetize or otherwise reorder keys).
_MANIFEST_KEY_ORDER = ("name", "title", "description")


def scaffold_tracker(cfg: Config, name: str) -> Path:
    """Create <root>/trackers/<name>/{manifest.yaml,schema.sql,ingest.py}.

    Raises FileExistsError if <root>/trackers/<name> already exists. Does not
    validate `name`'s format or check for collisions with bundled templates --
    callers that need friendlier validation (e.g. the web form) should check
    first with their own rules; this function's only guard is "don't clobber
    an existing directory," matching the pre-existing CLI behavior.
    """
    dest = cfg.trackers_dir / name
    if dest.exists():
        raise FileExistsError(f"already exists: {dest}")
    dest.mkdir(parents=True)
    (dest / "manifest.yaml").write_text(_SCAFFOLD_MANIFEST.format(name=name))
    (dest / "schema.sql").write_text(_SCAFFOLD_SCHEMA.format(name=name))
    (dest / "ingest.py").write_text(_SCAFFOLD_INGEST)
    return dest


def apply_manifest_overrides(
    manifest_path: Path, *, title: str | None = None, description: str | None = None
) -> None:
    """Patch `title`/`description` into a freshly scaffolded manifest.yaml, in place.

    No-op if neither is set. Round-trips through yaml.safe_load/safe_dump, so
    this is only meant for the scaffold's comment-free manifest -- it would
    strip comments from a hand-edited one.
    """
    if not title and not description:
        return
    data = yaml.safe_load(manifest_path.read_text()) or {}
    if title:
        data["title"] = title
    if description:
        data["description"] = description
    ordered: dict = {}
    for key in _MANIFEST_KEY_ORDER:
        if key in data:
            ordered[key] = data.pop(key)
    ordered.update(data)
    manifest_path.write_text(yaml.safe_dump(ordered, sort_keys=False))
