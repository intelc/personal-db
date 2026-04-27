# personal_db v0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Python-based personal data layer that (a) ingests data from 5 sources via per-tracker scripts, (b) exposes the data to Claude Code via an MCP server, (c) runs scheduled syncs via launchd, and (d) supports manual entry through an MCP tool — without a long-running daemon and without a UI.

**Architecture:** Single-process. Components are CLI invocations (`personal-db …`) or MCP requests (Claude Code spawns `personal-db mcp` over stdio per session). The "scheduler" is one launchd plist firing `personal-db sync --due` every 10 minutes. All persistent state lives under a user-configurable root (default `~/personal_db/`): `db.sqlite` is the single source of truth for data; per-tracker folders own their schemas and ingest scripts; entity registry mirrors from YAML files.

**Tech Stack:** Python 3.11+, `uv` (env + packaging), `typer` (CLI), `pydantic` v2 (validation), `pytest` (tests), `ruff` (lint+format), official `mcp` Python SDK (stdio server), `requests` (HTTP), `pyyaml` (YAML), `keyring` (encrypted token storage), stdlib `sqlite3`.

**Spec:** [`docs/superpowers/specs/2026-04-25-personal-db-v0-design.md`](../specs/2026-04-25-personal-db-v0-design.md)

---

## File structure (lock-in)

The package is one importable module with three sub-namespaces. Trackers ship as templates that `personal-db tracker install` copies into the user's data root.

```
personal_db/                              # repo root (cwd)
  pyproject.toml
  .gitignore .python-version ruff.toml pytest.ini
  README.md
  src/personal_db/
    __init__.py
    config.py                             # Config dataclass + loader (~/personal_db/config.yaml)
    db.py                                 # SQLite connection + migrations (entities, notes index)
    manifest.py                           # Pydantic Manifest model + load/validate
    tracker.py                            # Tracker class (cursor, upsert, resolve_*, config, log)
    entities.py                           # YAML <-> SQLite mirror, resolve_person/topic
    oauth.py                              # localhost callback, token storage, refresh
    permissions.py                        # FDA probe + open-system-settings helper
    scheduler.py                          # launchd plist gen + install/uninstall/status
    sync.py                               # sync_one(name), sync_due() dispatcher, backfill_one
    log_event.py                          # shared write path used by `personal-db log` and MCP `log_event`
    notes.py                              # write/list/read notes (used by MCP)
    cli/
      __init__.py
      main.py                             # typer app `personal-db`; wires sub-apps
      init_cmd.py                         # `personal-db init`
      tracker_cmd.py                      # `personal-db tracker new|list|install|setup`
      sync_cmd.py                         # `personal-db sync`, `personal-db backfill`
      log_cmd.py                          # `personal-db log`
      permission_cmd.py                   # `personal-db permission check`
      scheduler_cmd.py                    # `personal-db scheduler install|uninstall|status`
      mcp_cmd.py                          # `personal-db mcp`
    mcp_server/
      __init__.py
      server.py                           # MCP stdio bootstrap
      tools.py                            # tool implementations (one function each)
    templates/                            # bundled tracker templates
      trackers/
        github_commits/  (manifest.yaml, schema.sql, ingest.py)
        whoop/           (manifest.yaml, schema.sql, ingest.py)
        screen_time/     (manifest.yaml, schema.sql, ingest.py)
        imessage/        (manifest.yaml, schema.sql, ingest.py)
        habits/          (manifest.yaml, schema.sql, ingest.py)
      claude_skill/
        insights.md
  tests/
    conftest.py                           # tmp-root fixture + db fixture
    unit/
      test_config.py test_manifest.py test_db.py test_tracker.py
      test_entities.py test_oauth.py test_permissions.py
      test_scheduler.py test_notes.py test_sync.py
      test_mcp_tools.py test_log_event.py
    integration/
      test_cli_init.py test_cli_tracker.py test_cli_sync.py
      test_mcp_server.py test_e2e_smoke.py
    fixtures/
      github/  whoop/  screen_time/  imessage/
    live/                                 # opt-in (`pytest -m live`)
  scripts/
    install_dev.sh                        # one-shot dev setup
```

**Boundary discipline:** Each `*.py` should stay under ~400 LOC. Connectors' `ingest.py` files are owned by the tracker template, not by the framework — they import from `personal_db.tracker` only.

---

## Conventions used by every task

- **Branch:** all work happens on `main` (single-developer v0). No feature branches.
- **Commits:** one per task, message format `feat(area): summary` or `test(area):`/`refactor(area):` etc.
- **Test runner:** `uv run pytest` (or `pytest` if active venv).
- **Lint/format:** `uv run ruff check . && uv run ruff format .` before each commit.
- **TDD pattern per task:** write failing test → run, see it fail → write minimal implementation → run, see it pass → ruff → commit.
- **Read-only DB connections** are obtained via `db.connect(read_only=True)`; write connections via `db.connect()`.
- **Tmp-root fixture:** every test that touches the filesystem uses the `tmp_root` pytest fixture defined in `tests/conftest.py` (Task 2). Never hard-code `~/personal_db/`.

---

## Task list

### Task 1: Repo init + project skeleton

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.python-version`, `ruff.toml`, `pytest.ini`, `README.md`, `src/personal_db/__init__.py`, `tests/__init__.py`, `scripts/install_dev.sh`

- [ ] **Step 1: Initialize git and uv project**

```bash
cd ~/code/personal_db
git init
echo "3.11" > .python-version
uv init --package personal_db --no-readme
```

- [ ] **Step 2: Replace `pyproject.toml` contents**

```toml
[project]
name = "personal_db"
version = "0.1.0"
description = "Personal data layer for AI agents"
requires-python = ">=3.11"
dependencies = [
  "typer>=0.12",
  "pydantic>=2.6",
  "pyyaml>=6.0",
  "requests>=2.31",
  "keyring>=24.0",
  "mcp>=0.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.4", "pyright>=1.1"]

[project.scripts]
personal-db = "personal_db.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/personal_db"]
```

- [ ] **Step 3: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
*.egg-info/
dist/
build/
```

- [ ] **Step 4: Write `ruff.toml`**

```toml
line-length = 100
target-version = "py311"
[lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF"]
ignore = ["E501"]
```

- [ ] **Step 5: Write `pytest.ini`**

```ini
[pytest]
testpaths = tests
markers =
    live: tests that hit live external APIs (opt-in)
addopts = -ra --strict-markers -m "not live"
```

- [ ] **Step 6: Empty `src/personal_db/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 7: README stub**

Write `README.md` with one-paragraph summary, links to spec and plan, and "see install_dev.sh for setup."

- [ ] **Step 8: Dev setup script**

`scripts/install_dev.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
uv venv
uv pip install -e ".[dev]"
echo "Activate: source .venv/bin/activate"
```
`chmod +x scripts/install_dev.sh`

- [ ] **Step 9: Run setup, sanity-check imports**

```bash
./scripts/install_dev.sh
source .venv/bin/activate
python -c "import personal_db; print(personal_db.__version__)"
```
Expected: `0.1.0`

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "chore: initialize personal_db project skeleton"
```

---

### Task 2: Test conftest with tmp-root fixture

**Files:**
- Create: `tests/conftest.py`, `tests/unit/test_smoke.py`

- [ ] **Step 1: Write smoke test that uses tmp_root**

`tests/unit/test_smoke.py`:
```python
def test_tmp_root_fixture(tmp_root):
    assert tmp_root.exists()
    assert (tmp_root / "trackers").exists()
    assert (tmp_root / "entities").exists()
    assert (tmp_root / "notes").exists()
    assert (tmp_root / "state").exists()
```

- [ ] **Step 2: Run, confirm it fails (no fixture defined)**

```bash
pytest tests/unit/test_smoke.py -v
```
Expected: FAIL with `fixture 'tmp_root' not found`.

- [ ] **Step 3: Write conftest**

`tests/conftest.py`:
```python
from pathlib import Path
import pytest

@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """Fresh personal_db root for each test."""
    root = tmp_path / "personal_db"
    for sub in ("trackers", "entities", "notes", "state", "state/oauth"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
```

- [ ] **Step 4: Run, confirm pass**

```bash
pytest tests/unit/test_smoke.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: add tmp_root fixture for filesystem tests"
```

---

### Task 3: Config loader

**Files:**
- Create: `src/personal_db/config.py`, `tests/unit/test_config.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_config.py`:
```python
import yaml
from personal_db.config import Config, load_config, DEFAULT_ROOT

def test_default_root_path():
    assert DEFAULT_ROOT == "~/personal_db"

def test_load_config_with_explicit_root(tmp_root):
    cfg_path = tmp_root / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({"root": str(tmp_root)}))
    cfg = load_config(cfg_path)
    assert cfg.root == tmp_root
    assert cfg.db_path == tmp_root / "db.sqlite"
    assert cfg.trackers_dir == tmp_root / "trackers"
    assert cfg.entities_dir == tmp_root / "entities"
    assert cfg.notes_dir == tmp_root / "notes"
    assert cfg.state_dir == tmp_root / "state"

def test_load_config_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.root == Path("~/personal_db").expanduser()
```

- [ ] **Step 2: Run, confirm fail**

```bash
pytest tests/unit/test_config.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `src/personal_db/config.py`**

```python
from dataclasses import dataclass
from pathlib import Path
import yaml

DEFAULT_ROOT = "~/personal_db"

@dataclass(frozen=True)
class Config:
    root: Path

    @property
    def db_path(self) -> Path: return self.root / "db.sqlite"
    @property
    def trackers_dir(self) -> Path: return self.root / "trackers"
    @property
    def entities_dir(self) -> Path: return self.root / "entities"
    @property
    def notes_dir(self) -> Path: return self.root / "notes"
    @property
    def state_dir(self) -> Path: return self.root / "state"

def load_config(path: Path | None = None) -> Config:
    """Load config.yaml; fall back to defaults if missing."""
    if path is None:
        path = Path(DEFAULT_ROOT).expanduser() / "config.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    else:
        data = {}
    root = Path(data.get("root", DEFAULT_ROOT)).expanduser()
    return Config(root=root)
```

- [ ] **Step 4: Run, confirm pass**

```bash
pytest tests/unit/test_config.py -v
```

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(config): add Config dataclass and YAML loader"
```

---

### Task 4: Database setup + migrations

**Files:**
- Create: `src/personal_db/db.py`, `tests/unit/test_db.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_db.py`:
```python
import sqlite3
from personal_db.db import connect, init_db, CORE_TABLES

def test_init_db_creates_core_tables(tmp_root):
    db_path = tmp_root / "db.sqlite"
    init_db(db_path)
    con = connect(db_path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in CORE_TABLES:
        assert name in tables, f"missing core table {name}"

def test_connect_read_only_blocks_writes(tmp_root):
    db_path = tmp_root / "db.sqlite"
    init_db(db_path)
    con = connect(db_path, read_only=True)
    try:
        con.execute("CREATE TABLE x (a INT)")
        raise AssertionError("write should have failed")
    except sqlite3.OperationalError:
        pass
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/db.py`**

```python
import sqlite3
from pathlib import Path

CORE_TABLES = ("people", "people_aliases", "topics", "topics_aliases", "notes")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS people (
  person_id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS people_aliases (
  alias TEXT PRIMARY KEY,
  person_id INTEGER NOT NULL REFERENCES people(person_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS topics (
  topic_id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS topics_aliases (
  alias TEXT PRIMARY KEY,
  topic_id INTEGER NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS notes (
  path TEXT PRIMARY KEY,
  title TEXT,
  created_at TEXT NOT NULL,
  body_excerpt TEXT
);
"""

def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA_SQL)
    con.commit()
    con.close()

def connect(db_path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    return con

def apply_tracker_schema(db_path: Path, schema_sql: str) -> None:
    """Run a tracker's schema.sql against the main db."""
    con = sqlite3.connect(db_path)
    con.executescript(schema_sql)
    con.commit()
    con.close()
```

- [ ] **Step 4: Run, pass. Lint, commit.**

```bash
pytest tests/unit/test_db.py -v
ruff check . && ruff format .
git add -A
git commit -m "feat(db): add SQLite setup, core tables, read-only connect"
```

---

### Task 5: Manifest model + validator

**Files:**
- Create: `src/personal_db/manifest.py`, `tests/unit/test_manifest.py`, `tests/fixtures/manifest_valid.yaml`, `tests/fixtures/manifest_missing_time_column.yaml`

- [ ] **Step 1: Write fixtures**

`tests/fixtures/manifest_valid.yaml`:
```yaml
name: github_commits
description: Commits authored by the user across GitHub
permission_type: api_key
setup_steps:
  - "Set GITHUB_TOKEN env var with a personal access token (scope: read:user, repo)"
schedule:
  every: 4h
time_column: committed_at
granularity: event
schema:
  tables:
    github_commits:
      columns:
        sha: {type: TEXT, semantic: "commit SHA, primary key"}
        repo: {type: TEXT, semantic: "owner/name"}
        committed_at: {type: TEXT, semantic: "ISO-8601 commit timestamp (UTC)"}
        message: {type: TEXT, semantic: "first line of commit message"}
        additions: {type: INTEGER, semantic: "lines added"}
        deletions: {type: INTEGER, semantic: "lines deleted"}
related_entities: []
```

`tests/fixtures/manifest_missing_time_column.yaml`:
```yaml
name: bad_tracker
description: missing time column
permission_type: api_key
setup_steps: []
schedule: {every: 1h}
schema:
  tables:
    bad: {columns: {x: {type: INTEGER, semantic: "x"}}}
```

- [ ] **Step 2: Write failing tests**

`tests/unit/test_manifest.py`:
```python
from pathlib import Path
import pytest
from personal_db.manifest import load_manifest, ManifestError, Manifest

FIXTURES = Path(__file__).parent.parent / "fixtures"

def test_load_valid_manifest():
    m = load_manifest(FIXTURES / "manifest_valid.yaml")
    assert isinstance(m, Manifest)
    assert m.name == "github_commits"
    assert m.time_column == "committed_at"
    assert "github_commits" in m.schema.tables
    assert m.permission_type == "api_key"

def test_missing_time_column_rejected():
    with pytest.raises(ManifestError):
        load_manifest(FIXTURES / "manifest_missing_time_column.yaml")
```

- [ ] **Step 3: Run, fail.**

- [ ] **Step 4: Implement `src/personal_db/manifest.py`**

```python
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, Field, ValidationError

class ManifestError(Exception): ...

class ColumnSpec(BaseModel):
    type: str
    semantic: str

class TableSpec(BaseModel):
    columns: dict[str, ColumnSpec]

class SchemaSpec(BaseModel):
    tables: dict[str, TableSpec]

class ScheduleSpec(BaseModel):
    every: str | None = None
    cron: str | None = None

PermissionType = Literal["none", "api_key", "oauth", "full_disk_access", "manual"]

class Manifest(BaseModel):
    name: str
    description: str
    permission_type: PermissionType
    setup_steps: list[str] = Field(default_factory=list)
    schedule: ScheduleSpec | None = None
    time_column: str
    granularity: Literal["event", "minute", "hour", "day"] = "event"
    schema: SchemaSpec
    related_entities: list[str] = Field(default_factory=list)

def load_manifest(path: Path) -> Manifest:
    try:
        data = yaml.safe_load(path.read_text())
        return Manifest.model_validate(data)
    except (yaml.YAMLError, ValidationError) as e:
        raise ManifestError(f"{path}: {e}") from e
```

- [ ] **Step 5: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(manifest): add Pydantic Manifest model + loader"
```

---

### Task 6: Tracker class — cursor + upsert

**Files:**
- Create: `src/personal_db/tracker.py`, `tests/unit/test_tracker.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_tracker.py`:
```python
import sqlite3
import pytest
from personal_db.config import Config
from personal_db.db import init_db, apply_tracker_schema, connect
from personal_db.tracker import Tracker

@pytest.fixture
def cfg(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, """
        CREATE TABLE demo (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            value INTEGER
        );
    """)
    return cfg

def test_cursor_get_default_when_unset(cfg):
    t = Tracker(name="demo", cfg=cfg, manifest=None)  # manifest unused for cursor tests
    assert t.cursor.get(default="2020-01-01") == "2020-01-01"

def test_cursor_set_then_get(cfg):
    t = Tracker(name="demo", cfg=cfg, manifest=None)
    t.cursor.set("2026-04-01")
    assert t.cursor.get() == "2026-04-01"

def test_upsert_inserts_new_rows(cfg):
    t = Tracker(name="demo", cfg=cfg, manifest=None)
    t.upsert("demo", [{"id": "a", "ts": "2026-04-01", "value": 1}], key=["id"])
    con = connect(cfg.db_path)
    rows = con.execute("SELECT id, value FROM demo").fetchall()
    assert rows == [("a", 1)]

def test_upsert_updates_existing_rows(cfg):
    t = Tracker(name="demo", cfg=cfg, manifest=None)
    t.upsert("demo", [{"id": "a", "ts": "2026-04-01", "value": 1}], key=["id"])
    t.upsert("demo", [{"id": "a", "ts": "2026-04-01", "value": 2}], key=["id"])
    con = connect(cfg.db_path)
    assert con.execute("SELECT value FROM demo WHERE id='a'").fetchone() == (2,)
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/tracker.py`**

```python
from __future__ import annotations
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personal_db.config import Config
from personal_db.db import connect

class Cursor:
    """Per-tracker cursor stored in state/cursors.sqlite."""
    def __init__(self, name: str, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self._db = state_dir / "cursors.sqlite"
        self._name = name
        self._init()

    def _init(self) -> None:
        con = sqlite3.connect(self._db)
        con.execute("CREATE TABLE IF NOT EXISTS cursors (name TEXT PRIMARY KEY, value TEXT)")
        con.commit(); con.close()

    def get(self, default: str | None = None) -> str | None:
        con = sqlite3.connect(self._db)
        row = con.execute("SELECT value FROM cursors WHERE name=?", (self._name,)).fetchone()
        con.close()
        return row[0] if row else default

    def set(self, value: str) -> None:
        con = sqlite3.connect(self._db)
        con.execute(
            "INSERT INTO cursors(name,value) VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (self._name, value),
        )
        con.commit(); con.close()


@dataclass
class Tracker:
    name: str
    cfg: Config
    manifest: Any  # Manifest | None — None permitted in tests/cursor-only contexts
    log: logging.Logger = field(init=False)
    cursor: Cursor = field(init=False)

    def __post_init__(self):
        self.log = logging.getLogger(f"personal_db.tracker.{self.name}")
        self.cursor = Cursor(self.name, self.cfg.state_dir)

    def upsert(self, table: str, rows: list[dict], key: list[str]) -> int:
        if not rows:
            return 0
        cols = list(rows[0].keys())
        placeholders = ",".join("?" * len(cols))
        update_cols = [c for c in cols if c not in key]
        if update_cols:
            update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
            sql = (
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT({','.join(key)}) DO UPDATE SET {update_clause}"
            )
        else:
            sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        con = connect(self.cfg.db_path)
        con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        con.commit()
        n = con.total_changes
        con.close()
        return n
```

- [ ] **Step 4: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(tracker): add Tracker class with Cursor and upsert"
```

---

### Task 7: Entity registry — YAML loader + resolver + SQLite mirror

**Files:**
- Create: `src/personal_db/entities.py`, `tests/unit/test_entities.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_entities.py`:
```python
import yaml
from personal_db.config import Config
from personal_db.db import init_db, connect
from personal_db.entities import (
    EntityStore, sync_entities_from_yaml, resolve_person, resolve_topic
)

def write_people_yaml(tmp_root, data):
    p = tmp_root / "entities" / "people.yaml"
    p.write_text(yaml.safe_dump(data))
    return p

def test_sync_creates_people_and_aliases(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    write_people_yaml(tmp_root, [
        {"display_name": "Marko Chen", "aliases": ["marko@example.com", "+15551234567"]},
    ])
    (tmp_root / "entities" / "topics.yaml").write_text("[]")
    sync_entities_from_yaml(cfg)
    con = connect(cfg.db_path)
    assert con.execute("SELECT display_name FROM people").fetchall() == [("Marko Chen",)]
    aliases = {r[0] for r in con.execute("SELECT alias FROM people_aliases")}
    assert aliases == {"marko@example.com", "+15551234567"}

def test_resolve_person_existing_alias(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    write_people_yaml(tmp_root, [
        {"display_name": "Marko Chen", "aliases": ["marko@example.com"]},
    ])
    (tmp_root / "entities" / "topics.yaml").write_text("[]")
    sync_entities_from_yaml(cfg)
    pid = resolve_person(cfg, "marko@example.com")
    assert pid is not None

def test_resolve_person_unknown_auto_creates(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    (tmp_root / "entities" / "people.yaml").write_text("[]")
    (tmp_root / "entities" / "topics.yaml").write_text("[]")
    sync_entities_from_yaml(cfg)
    pid = resolve_person(cfg, "newperson@example.com", auto_create=True)
    assert pid is not None
    needs_review = (tmp_root / "state" / "entities_needs_review.jsonl").read_text()
    assert "newperson@example.com" in needs_review
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/entities.py`**

```python
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
import yaml

from personal_db.config import Config
from personal_db.db import connect

class EntityStore:
    """Helper namespace; keep state inside the SQLite db."""

def sync_entities_from_yaml(cfg: Config) -> None:
    """Reconcile people/topics tables to the YAML files (YAML is source of truth).
    Writes-through: any DB-only entries (e.g. auto-created) are kept;
    any YAML entry is upserted. Aliases are fully replaced from YAML for each person."""
    con = connect(cfg.db_path)
    for kind, table_e, table_a, id_col, yaml_name in (
        ("people", "people", "people_aliases", "person_id", "people.yaml"),
        ("topics", "topics", "topics_aliases", "topic_id", "topics.yaml"),
    ):
        path = cfg.entities_dir / yaml_name
        if not path.exists():
            continue
        entries = yaml.safe_load(path.read_text()) or []
        for entry in entries:
            display = entry["display_name"]
            row = con.execute(
                f"SELECT {id_col} FROM {table_e} WHERE display_name=?", (display,)
            ).fetchone()
            if row:
                eid = row[0]
            else:
                cur = con.execute(
                    f"INSERT INTO {table_e}(display_name) VALUES (?)", (display,)
                )
                eid = cur.lastrowid
            for alias in entry.get("aliases", []):
                con.execute(
                    f"INSERT OR IGNORE INTO {table_a}(alias,{id_col}) VALUES (?,?)",
                    (alias, eid),
                )
    con.commit(); con.close()

def _resolve(cfg: Config, alias: str, kind: str, *, auto_create: bool) -> int | None:
    table_a = f"{kind}_aliases"
    table_e = kind
    id_col = "person_id" if kind == "people" else "topic_id"
    con = connect(cfg.db_path)
    row = con.execute(
        f"SELECT {id_col} FROM {table_a} WHERE alias=?", (alias,)
    ).fetchone()
    if row:
        con.close()
        return row[0]
    if not auto_create:
        con.close()
        return None
    cur = con.execute(f"INSERT INTO {table_e}(display_name) VALUES (?)", (alias,))
    eid = cur.lastrowid
    con.execute(f"INSERT INTO {table_a}(alias,{id_col}) VALUES (?,?)", (alias, eid))
    con.commit(); con.close()
    review_path = cfg.state_dir / "entities_needs_review.jsonl"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    with review_path.open("a") as f:
        f.write(json.dumps({
            "kind": kind, "alias": alias, "id": eid,
            "ts": datetime.utcnow().isoformat()
        }) + "\n")
    return eid

def resolve_person(cfg: Config, alias: str, *, auto_create: bool = True) -> int | None:
    return _resolve(cfg, alias, "people", auto_create=auto_create)

def resolve_topic(cfg: Config, alias: str, *, auto_create: bool = True) -> int | None:
    return _resolve(cfg, alias, "topics", auto_create=auto_create)
```

- [ ] **Step 4: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(entities): YAML<->SQLite mirror, resolve_person/topic with auto-create"
```

---

### Task 8: Wire entity resolution into Tracker

**Files:**
- Modify: `src/personal_db/tracker.py`
- Modify: `tests/unit/test_tracker.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/test_tracker.py`:
```python
from personal_db.entities import sync_entities_from_yaml
import yaml

def test_tracker_resolve_person(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    (tmp_root / "entities" / "people.yaml").write_text(
        yaml.safe_dump([{"display_name": "Marko", "aliases": ["marko@example.com"]}])
    )
    (tmp_root / "entities" / "topics.yaml").write_text("[]")
    sync_entities_from_yaml(cfg)
    t = Tracker(name="demo", cfg=cfg, manifest=None)
    pid = t.resolve_person("marko@example.com")
    assert pid is not None
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Add methods to `Tracker`**

In `src/personal_db/tracker.py`, add to the `Tracker` class:
```python
    def resolve_person(self, alias: str, *, auto_create: bool = True) -> int | None:
        from personal_db.entities import resolve_person
        return resolve_person(self.cfg, alias, auto_create=auto_create)

    def resolve_topic(self, alias: str, *, auto_create: bool = True) -> int | None:
        from personal_db.entities import resolve_topic
        return resolve_topic(self.cfg, alias, auto_create=auto_create)
```

- [ ] **Step 4: Run, pass. Commit.**

```bash
git add -A
git commit -m "feat(tracker): expose resolve_person/topic on Tracker"
```

---

### Task 9: Sync engine (single + due dispatcher)

**Files:**
- Create: `src/personal_db/sync.py`, `tests/unit/test_sync.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_sync.py`:
```python
import json, time
from pathlib import Path
import yaml
import pytest

from personal_db.config import Config
from personal_db.db import init_db
from personal_db.sync import sync_one, sync_due, _is_due

def _make_tracker_dir(tmp_root: Path, name: str, schedule_every: str = "1h"):
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "name": name, "description": "test",
        "permission_type": "none",
        "setup_steps": [],
        "schedule": {"every": schedule_every},
        "time_column": "ts",
        "granularity": "event",
        "schema": {"tables": {name: {"columns": {
            "id": {"type": "TEXT", "semantic": "id"},
            "ts": {"type": "TEXT", "semantic": "ts"},
        }}}},
    }))
    (d / "schema.sql").write_text(f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, ts TEXT);")
    (d / "ingest.py").write_text(
        "def backfill(t, start, end):\n"
        "    t.upsert(t.name, [{'id': 'b1', 'ts': '2026-04-01'}], key=['id'])\n"
        "def sync(t):\n"
        "    t.upsert(t.name, [{'id': 's1', 'ts': '2026-04-25'}], key=['id'])\n"
    )
    return d

def test_sync_one_runs_ingest_and_records_last_run(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(tmp_root, "demo")
    sync_one(cfg, "demo")
    last_run = json.loads((tmp_root / "state" / "last_run.json").read_text())
    assert "demo" in last_run

def test_is_due_when_never_run(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(tmp_root, "demo", schedule_every="1h")
    assert _is_due(cfg, "demo") is True

def test_sync_due_skips_recent(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(tmp_root, "demo", schedule_every="1h")
    sync_one(cfg, "demo")
    # Immediately due-check should be false
    assert _is_due(cfg, "demo") is False
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/sync.py`**

```python
from __future__ import annotations
import importlib.util
import json
import re
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from personal_db.config import Config
from personal_db.db import apply_tracker_schema, init_db
from personal_db.manifest import load_manifest, Manifest
from personal_db.tracker import Tracker

_EVERY_RE = re.compile(r"^(\d+)\s*([smhd])$")

def _parse_every(s: str) -> timedelta:
    m = _EVERY_RE.match(s.strip())
    if not m:
        raise ValueError(f"bad schedule.every: {s!r}")
    n, unit = int(m.group(1)), m.group(2)
    return {"s": timedelta(seconds=n), "m": timedelta(minutes=n),
            "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]

def _load_ingest_module(tracker_dir: Path, name: str):
    """Load ingest.py fresh on every call. Drop any prior cached version so
    tests that re-create trackers under tmp_path don't see stale code."""
    spec_name = f"personal_db_trackers_{name}"
    sys.modules.pop(spec_name, None)
    spec = importlib.util.spec_from_file_location(spec_name, tracker_dir / "ingest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = mod
    spec.loader.exec_module(mod)
    return mod

def _last_run_path(cfg: Config) -> Path:
    return cfg.state_dir / "last_run.json"

def _read_last_run(cfg: Config) -> dict[str, str]:
    p = _last_run_path(cfg)
    return json.loads(p.read_text()) if p.exists() else {}

def _write_last_run(cfg: Config, name: str, ts: str) -> None:
    data = _read_last_run(cfg)
    data[name] = ts
    _last_run_path(cfg).write_text(json.dumps(data, indent=2))

def _is_due(cfg: Config, name: str) -> bool:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    if not manifest.schedule or not manifest.schedule.every:
        return False  # cron schedules: launchd handles cadence; we always run when called
    last = _read_last_run(cfg).get(name)
    if not last:
        return True
    delta = _parse_every(manifest.schedule.every)
    return datetime.fromisoformat(last) + delta <= datetime.utcnow()

def _ensure_schema(cfg: Config, tracker_dir: Path) -> None:
    init_db(cfg.db_path)
    schema_sql = (tracker_dir / "schema.sql").read_text()
    apply_tracker_schema(cfg.db_path, schema_sql)

def sync_one(cfg: Config, name: str) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.sync(t)
    _write_last_run(cfg, name, datetime.utcnow().isoformat())

def backfill_one(cfg: Config, name: str, start: str | None, end: str | None) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.backfill(t, start, end)

def sync_due(cfg: Config) -> dict[str, str]:
    """Run every due tracker. Returns {name: 'ok'|'<error>'}."""
    results: dict[str, str] = {}
    for tracker_dir in sorted(cfg.trackers_dir.iterdir()):
        if not tracker_dir.is_dir():
            continue
        name = tracker_dir.name
        try:
            if _is_due(cfg, name):
                sync_one(cfg, name)
                results[name] = "ok"
            else:
                results[name] = "skip"
        except Exception as e:
            results[name] = f"error: {e}"
            err_path = cfg.state_dir / "sync_errors.jsonl"
            with err_path.open("a") as f:
                f.write(json.dumps({
                    "ts": datetime.utcnow().isoformat(),
                    "tracker": name, "error": str(e),
                    "tb": traceback.format_exc(),
                }) + "\n")
    return results
```

- [ ] **Step 4: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(sync): sync_one, backfill_one, sync_due dispatcher with isolation"
```

---

### Task 10: CLI scaffold + `personal-db init`

**Files:**
- Create: `src/personal_db/cli/__init__.py`, `src/personal_db/cli/main.py`, `src/personal_db/cli/init_cmd.py`, `tests/integration/test_cli_init.py`

**Note on CLI argument order:** `--root` is a *global* option defined on the parent app's callback, so it must always appear **before** the subcommand:
- ✅ `personal-db --root /tmp/foo init`
- ❌ `personal-db init --root /tmp/foo` (typer will reject this)

Every test in this plan and every example in the README follows the global-first convention. Document this in the README's quick-start.

- [ ] **Step 1: Write failing test**

`tests/integration/test_cli_init.py`:
```python
import subprocess, sys

def test_pdb_init_creates_root(tmp_path):
    root = tmp_path / "personal_db"
    result = subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "config.yaml").exists()
    assert (root / "trackers").exists()
    assert (root / "entities" / "people.yaml").exists()
    assert (root / "entities" / "topics.yaml").exists()
    assert (root / "notes").exists()
    assert (root / "state").exists()
    assert (root / "db.sqlite").exists()
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement CLI scaffold with the global `--root` callback**

`src/personal_db/cli/__init__.py`: empty.

`src/personal_db/cli/main.py`:
```python
from pathlib import Path
import typer

from personal_db.cli import init_cmd

app = typer.Typer(no_args_is_help=True, help="Personal data layer CLI")

# Global state — set in callback, read by commands via get_root().
_state: dict[str, Path | None] = {"root": None}

def get_root() -> Path:
    if _state["root"] is None:
        return Path("~/personal_db").expanduser()
    return _state["root"]

@app.callback()
def _global(root: str = typer.Option(None, "--root", help="Override data root")):
    if root:
        _state["root"] = Path(root).expanduser()

app.command("init")(init_cmd.run)

if __name__ == "__main__":
    app()
```

`src/personal_db/cli/init_cmd.py`:
```python
import yaml
import typer

from personal_db.cli import main as cli_main
from personal_db.db import init_db

def run() -> None:
    """Initialize a personal_db root directory.

    The root is taken from the global `--root` option (see `personal-db --help`),
    falling back to `~/personal_db`.
    """
    root_p = cli_main.get_root()
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
```

- [ ] **Step 4: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(cli): personal-db init creates root + config + entity skeletons"
```

---

### Task 11: `personal-db tracker new|list|install`

**Files:**
- Create: `src/personal_db/cli/tracker_cmd.py`, `tests/integration/test_cli_tracker.py`
- Modify: `src/personal_db/cli/main.py`

- [ ] **Step 1: Write failing tests**

`tests/integration/test_cli_tracker.py`:
```python
import subprocess, sys, yaml
from pathlib import Path

def _init(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True, capture_output=True,
    )
    return root

def test_tracker_new_scaffolds_files(tmp_path):
    root = _init(tmp_path)
    r = subprocess.run([
        sys.executable, "-m", "personal_db.cli.main",
        "--root", str(root), "tracker", "new", "my_metric",
    ], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    d = root / "trackers" / "my_metric"
    assert (d / "manifest.yaml").exists()
    assert (d / "schema.sql").exists()
    assert (d / "ingest.py").exists()
    m = yaml.safe_load((d / "manifest.yaml").read_text())
    assert m["name"] == "my_metric"

def test_tracker_list_empty(tmp_path):
    root = _init(tmp_path)
    r = subprocess.run([
        sys.executable, "-m", "personal_db.cli.main",
        "--root", str(root), "tracker", "list",
    ], capture_output=True, text=True)
    assert r.returncode == 0
    assert "No trackers" in r.stdout
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Wire tracker subcommands into main.py**

Append to `src/personal_db/cli/main.py` (the global `--root` callback already exists from Task 10):
```python
from personal_db.cli import tracker_cmd

tracker_app = typer.Typer(no_args_is_help=True, help="Tracker management")
tracker_app.command("new")(tracker_cmd.new)
tracker_app.command("list")(tracker_cmd.list_cmd)
tracker_app.command("install")(tracker_cmd.install)
app.add_typer(tracker_app, name="tracker")
```

- [ ] **Step 4: Implement `tracker_cmd.py`**

```python
from importlib import resources
from pathlib import Path
import shutil
import yaml
import typer

from personal_db.cli import main as cli_main
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
    root = cli_main.get_root()
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
    root = cli_main.get_root()
    trackers_dir = root / "trackers"
    if not trackers_dir.exists() or not any(trackers_dir.iterdir()):
        typer.echo("No trackers installed. Use `personal-db tracker new <name>` or `personal-db tracker install <builtin>`.")
        return
    for d in sorted(trackers_dir.iterdir()):
        if d.is_dir() and (d / "manifest.yaml").exists():
            m = load_manifest(d / "manifest.yaml")
            typer.echo(f"  {m.name:20s} {m.permission_type:18s} {m.description}")

def install(name: str) -> None:
    """Copy a bundled tracker template into the user's trackers/ directory."""
    root = cli_main.get_root()
    dest = root / "trackers" / name
    if dest.exists():
        typer.echo(f"already installed: {dest}", err=True); raise typer.Exit(1)
    src_pkg = resources.files("personal_db.templates.trackers").joinpath(name)
    if not src_pkg.is_dir():
        typer.echo(f"unknown built-in tracker: {name}", err=True); raise typer.Exit(1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(src_pkg) as src_path:
        shutil.copytree(src_path, dest)
    typer.echo(f"Installed {name} -> {dest}")
```

- [ ] **Step 5: Add an empty `src/personal_db/templates/__init__.py` and `templates/trackers/__init__.py`**

So they're packageable resources. Repeat for any tracker subdir we add later.

- [ ] **Step 6: Update `pyproject.toml` to include templates in the wheel**

Under `[tool.hatch.build.targets.wheel]`, add:
```toml
include = ["src/personal_db/templates/**/*"]
```

- [ ] **Step 7: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(cli): tracker new/list/install commands + template resources"
```

---

### Task 12: `personal-db sync`, `personal-db backfill`, `personal-db log`

**Files:**
- Create: `src/personal_db/cli/sync_cmd.py`, `src/personal_db/cli/log_cmd.py`, `src/personal_db/log_event.py`, `tests/integration/test_cli_sync.py`, `tests/unit/test_log_event.py`
- Modify: `src/personal_db/cli/main.py`

- [ ] **Step 1: Write failing tests for log_event**

`tests/unit/test_log_event.py`:
```python
import yaml
from personal_db.config import Config
from personal_db.db import init_db, apply_tracker_schema, connect
from personal_db.log_event import log_event

def _setup_habits(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / "habits"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "name": "habits", "description": "habit log",
        "permission_type": "manual", "setup_steps": [],
        "time_column": "ts", "granularity": "event",
        "schema": {"tables": {"habits": {"columns": {
            "id":    {"type": "INTEGER", "semantic": "rowid"},
            "name":  {"type": "TEXT",    "semantic": "habit name"},
            "value": {"type": "INTEGER", "semantic": "1=did, 0=didnt"},
            "ts":    {"type": "TEXT",    "semantic": "event time"},
        }}}},
    }))
    apply_tracker_schema(cfg.db_path,
        "CREATE TABLE habits (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER, ts TEXT);")
    return cfg

def test_log_event_inserts_row(tmp_root):
    cfg = _setup_habits(tmp_root)
    log_event(cfg, "habits", {"name": "meditate", "value": 1, "ts": "2026-04-25T08:00"})
    con = connect(cfg.db_path)
    row = con.execute("SELECT name,value FROM habits").fetchone()
    assert row == ("meditate", 1)

def test_log_event_rejects_unknown_field(tmp_root):
    cfg = _setup_habits(tmp_root)
    import pytest
    with pytest.raises(ValueError):
        log_event(cfg, "habits", {"name": "x", "value": 1, "ts": "now", "ghost": 7})
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/log_event.py`**

```python
from personal_db.config import Config
from personal_db.db import connect
from personal_db.manifest import load_manifest

def log_event(cfg: Config, tracker: str, fields: dict) -> int:
    manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
    # find the primary table — by convention, the table whose name matches the tracker name,
    # else the first table in the manifest
    tables = manifest.schema.tables
    table_name = tracker if tracker in tables else next(iter(tables))
    declared = set(tables[table_name].columns.keys())
    extra = set(fields) - declared
    if extra:
        raise ValueError(f"unknown field(s) for {tracker}.{table_name}: {sorted(extra)}")
    cols = list(fields.keys())
    placeholders = ",".join("?" * len(cols))
    con = connect(cfg.db_path)
    cur = con.execute(
        f"INSERT INTO {table_name} ({','.join(cols)}) VALUES ({placeholders})",
        tuple(fields[c] for c in cols),
    )
    con.commit()
    rowid = cur.lastrowid
    con.close()
    return rowid
```

- [ ] **Step 4: Write failing CLI test**

`tests/integration/test_cli_sync.py`:
```python
import subprocess, sys, yaml
from pathlib import Path

def _init_with_tracker(tmp_path, ingest_body: str):
    root = tmp_path / "personal_db"
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "init"], check=True, capture_output=True)
    d = root / "trackers" / "demo"; d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "name": "demo", "description": "x", "permission_type": "none",
        "setup_steps": [], "schedule": {"every": "1h"},
        "time_column": "ts", "granularity": "event",
        "schema": {"tables": {"demo": {"columns": {
            "id": {"type": "TEXT", "semantic": "id"},
            "ts": {"type": "TEXT", "semantic": "ts"},
        }}}},
    }))
    (d / "schema.sql").write_text("CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY, ts TEXT);")
    (d / "ingest.py").write_text(ingest_body)
    return root

def test_pdb_sync_runs_ingest(tmp_path):
    root = _init_with_tracker(tmp_path,
        "def backfill(t,start,end): pass\n"
        "def sync(t): t.upsert('demo', [{'id':'a','ts':'2026-04-25'}], key=['id'])\n")
    r = subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                        "--root", str(root), "sync", "demo"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    import sqlite3
    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT id FROM demo").fetchone() == ("a",)

def test_pdb_log_inserts_row(tmp_path):
    root = _init_with_tracker(tmp_path,
        "def backfill(t,start,end): pass\n"
        "def sync(t): pass\n")
    # Need an autoinc id since manifest test schema only has id+ts; use ts as id
    r = subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                        "--root", str(root), "log", "demo", "id=manual1", "ts=2026-04-25"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
```

- [ ] **Step 5: Implement sync_cmd and log_cmd, wire into main.py**

`src/personal_db/cli/sync_cmd.py`:
```python
import typer
from personal_db.cli import main as cli_main
from personal_db.config import Config
from personal_db.sync import sync_one, sync_due, backfill_one

def sync(
    name: str = typer.Argument(None),
    due: bool = typer.Option(False, "--due", help="Run only trackers that are due"),
) -> None:
    """Run sync for a tracker, or all due trackers."""
    cfg = Config(root=cli_main.get_root())
    if due:
        results = sync_due(cfg)
        for n, status in results.items():
            typer.echo(f"  {n}: {status}")
    elif name:
        sync_one(cfg, name)
        typer.echo(f"synced {name}")
    else:
        typer.echo("specify a tracker name or --due", err=True); raise typer.Exit(2)

def backfill(
    name: str = typer.Argument(...),
    from_: str = typer.Option(None, "--from"),
    to: str = typer.Option(None, "--to"),
) -> None:
    cfg = Config(root=cli_main.get_root())
    backfill_one(cfg, name, from_, to)
    typer.echo(f"backfilled {name}")
```

`src/personal_db/cli/log_cmd.py`:
```python
import typer
from personal_db.cli import main as cli_main
from personal_db.config import Config
from personal_db.log_event import log_event

def log(
    tracker: str = typer.Argument(...),
    fields: list[str] = typer.Argument(..., help="key=value pairs"),
) -> None:
    cfg = Config(root=cli_main.get_root())
    parsed = {}
    for pair in fields:
        if "=" not in pair:
            typer.echo(f"bad field: {pair!r} (expected key=value)", err=True); raise typer.Exit(2)
        k, v = pair.split("=", 1)
        parsed[k] = v
    rowid = log_event(cfg, tracker, parsed)
    typer.echo(f"logged {tracker} rowid={rowid}")
```

In `main.py`, add:
```python
from personal_db.cli import sync_cmd, log_cmd
app.command("sync")(sync_cmd.sync)
app.command("backfill")(sync_cmd.backfill)
app.command("log")(log_cmd.log)
```

- [ ] **Step 6: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(cli): personal-db sync/backfill/log + log_event write path"
```

---

### Task 13: Permissions helper + `personal-db permission check`

**Files:**
- Create: `src/personal_db/permissions.py`, `src/personal_db/cli/permission_cmd.py`, `tests/unit/test_permissions.py`
- Modify: `src/personal_db/cli/main.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_permissions.py`:
```python
import sqlite3
from pathlib import Path
import pytest
from personal_db.permissions import probe_sqlite_access, PermissionResult

def test_probe_returns_ok_for_accessible_db(tmp_path):
    p = tmp_path / "ok.sqlite"
    sqlite3.connect(p).execute("CREATE TABLE x(a)").connection.commit()
    r = probe_sqlite_access(p)
    assert r.granted is True

def test_probe_returns_denied_for_missing_path(tmp_path):
    r = probe_sqlite_access(tmp_path / "nope.sqlite")
    assert r.granted is False
    assert "missing" in r.reason.lower() or "no such" in r.reason.lower()
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/permissions.py`**

```python
from __future__ import annotations
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

@dataclass
class PermissionResult:
    granted: bool
    reason: str

def probe_sqlite_access(path: Path) -> PermissionResult:
    """Try to open a SQLite file read-only. Distinguish FDA-deny from missing."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        con.close()
        return PermissionResult(granted=True, reason="ok")
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "authorization denied" in msg or "operation not permitted" in msg:
            return PermissionResult(granted=False, reason=f"FDA denied: {e}")
        if "unable to open" in msg and not path.exists():
            return PermissionResult(granted=False, reason=f"file missing: {path}")
        return PermissionResult(granted=False, reason=str(e))

def open_fda_settings_pane() -> None:
    """Open System Settings -> Privacy & Security -> Full Disk Access."""
    subprocess.run([
        "open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    ], check=False)
```

- [ ] **Step 4: Implement permission_cmd.py**

```python
import typer
from pathlib import Path
from personal_db.cli import main as cli_main
from personal_db.config import Config
from personal_db.manifest import load_manifest
from personal_db.permissions import probe_sqlite_access, open_fda_settings_pane

# Trackers we know need to read a specific gated SQLite file.
# Connectors declare this via manifest.permission_type + a probe path in their setup_steps;
# for v0 we hard-code the two known FDA-gated ones.
FDA_PROBES = {
    "screen_time": Path("~/Library/Application Support/Knowledge/knowledgeC.db").expanduser(),
    "imessage":    Path("~/Library/Messages/chat.db").expanduser(),
}

def check(tracker: str = typer.Argument(...)) -> None:
    """Probe whether the OS permission required by this tracker is granted."""
    cfg = Config(root=cli_main.get_root())
    manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
    if manifest.permission_type != "full_disk_access":
        typer.echo(f"{tracker}: permission_type={manifest.permission_type} (no probe needed)")
        return
    probe_path = FDA_PROBES.get(tracker)
    if probe_path is None:
        typer.echo(f"no FDA probe path registered for {tracker}", err=True); raise typer.Exit(2)
    r = probe_sqlite_access(probe_path)
    typer.echo(f"{tracker}: granted={r.granted} -- {r.reason}")
    if not r.granted:
        typer.echo("Opening System Settings -> Privacy & Security -> Full Disk Access...")
        typer.echo("Grant access to your terminal binary (Terminal.app/iTerm2/Cursor),")
        typer.echo("then restart the terminal and re-run.")
        open_fda_settings_pane()
        raise typer.Exit(1)
```

In `main.py`:
```python
from personal_db.cli import permission_cmd
permission_app = typer.Typer(no_args_is_help=True, help="OS permission helpers")
permission_app.command("check")(permission_cmd.check)
app.add_typer(permission_app, name="permission")
```

- [ ] **Step 5: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(permissions): probe + System Settings opener + personal-db permission check"
```

---

### Task 14: Scheduler — launchd plist + `personal-db scheduler install/uninstall/status`

**Files:**
- Create: `src/personal_db/scheduler.py`, `src/personal_db/cli/scheduler_cmd.py`, `tests/unit/test_scheduler.py`
- Modify: `src/personal_db/cli/main.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_scheduler.py`:
```python
from pathlib import Path
from personal_db.scheduler import build_plist, LABEL

def test_build_plist_contains_label_and_interval():
    body = build_plist(pdb_path="/usr/local/bin/personal-db",
                       root=Path("/Users/me/personal_db"),
                       interval_seconds=600,
                       log_path=Path("/Users/me/personal_db/state/scheduler.log"))
    assert f"<string>{LABEL}</string>" in body
    assert "<integer>600</integer>" in body
    assert "/usr/local/bin/personal-db" in body
    assert "/Users/me/personal_db/state/scheduler.log" in body
    assert "<string>sync</string>" in body
    assert "<string>--due</string>" in body
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/scheduler.py`**

```python
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path

LABEL = "com.personal_db.scheduler"

def plist_path() -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"

def build_plist(pdb_path: str, root: Path, interval_seconds: int, log_path: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{pdb_path}</string>
    <string>--root</string><string>{root}</string>
    <string>sync</string>
    <string>--due</string>
  </array>
  <key>StartInterval</key><integer>{interval_seconds}</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{log_path}</string>
  <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""

def install(root: Path, interval_seconds: int = 600) -> Path:
    pdb_path = shutil.which("personal-db") or "personal-db"
    log_path = root / "state" / "scheduler.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_plist(pdb_path, root, interval_seconds, log_path))
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)  # ignore if not loaded
    subprocess.run(["launchctl", "load", str(p)], check=True)
    return p

def uninstall() -> None:
    p = plist_path()
    if p.exists():
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        p.unlink()

def status() -> str:
    p = plist_path()
    if not p.exists():
        return "not installed"
    r = subprocess.run(
        ["launchctl", "list", LABEL], capture_output=True, text=True
    )
    if r.returncode != 0:
        return f"plist exists but not loaded: {p}"
    return r.stdout
```

- [ ] **Step 4: Implement scheduler_cmd.py**

```python
import typer
from personal_db.cli import main as cli_main
from personal_db import scheduler

def install(interval_seconds: int = typer.Option(600, "--interval-seconds")) -> None:
    """Write a launchd plist and load it. Runs `personal-db sync --due` every interval."""
    p = scheduler.install(cli_main.get_root(), interval_seconds)
    typer.echo(f"installed: {p}")

def uninstall() -> None:
    scheduler.uninstall()
    typer.echo("uninstalled")

def status() -> None:
    typer.echo(scheduler.status())
```

In `main.py`:
```python
from personal_db.cli import scheduler_cmd
sched_app = typer.Typer(no_args_is_help=True, help="Background scheduler")
sched_app.command("install")(scheduler_cmd.install)
sched_app.command("uninstall")(scheduler_cmd.uninstall)
sched_app.command("status")(scheduler_cmd.status)
app.add_typer(sched_app, name="scheduler")
```

- [ ] **Step 5: Run unit tests (don't actually load launchd in tests). Lint. Commit.**

```bash
pytest tests/unit/test_scheduler.py -v
git add -A
git commit -m "feat(scheduler): launchd plist generation + install/uninstall/status"
```

---

### Task 15: OAuth helper

**Files:**
- Create: `src/personal_db/oauth.py`, `tests/unit/test_oauth.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_oauth.py`:
```python
import threading, time, urllib.request, json
from pathlib import Path
from personal_db.oauth import (
    OAuthFlow, save_token, load_token, refresh_if_needed
)
from personal_db.config import Config

def test_save_and_load_token(tmp_root):
    cfg = Config(root=tmp_root)
    save_token(cfg, "whoop", {"access_token": "a", "refresh_token": "r", "expires_at": 9999999999})
    t = load_token(cfg, "whoop")
    assert t["access_token"] == "a"

def test_callback_captures_code():
    """Spin up the callback server, hit it with a code, assert capture."""
    flow = OAuthFlow(state="xyz", port=0)  # port=0 -> ephemeral
    flow.start()
    try:
        url = f"http://127.0.0.1:{flow.port}/callback?state=xyz&code=abc123"
        urllib.request.urlopen(url, timeout=2).read()
        code = flow.wait_for_code(timeout_s=2)
        assert code == "abc123"
    finally:
        flow.shutdown()
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/oauth.py`**

```python
from __future__ import annotations
import http.server
import json
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any
import requests

from personal_db.config import Config

class OAuthFlow:
    """Local HTTP server that captures the OAuth callback ?code=…&state=…."""
    def __init__(self, state: str, port: int = 0):
        self._state_param = state
        self._code: str | None = None
        self._event = threading.Event()

        flow = self
        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                qs = urllib.parse.urlparse(self.path).query
                params = dict(urllib.parse.parse_qsl(qs))
                if params.get("state") != flow._state_param:
                    self.send_response(400); self.end_headers()
                    self.wfile.write(b"state mismatch"); return
                flow._code = params.get("code")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain"); self.end_headers()
                self.wfile.write(b"OK. You may close this tab.")
                flow._event.set()
            def log_message(self, *a): pass

        self._server = socketserver.TCPServer(("127.0.0.1", port), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self): self._thread.start()
    def wait_for_code(self, timeout_s: float = 120) -> str | None:
        self._event.wait(timeout=timeout_s)
        return self._code
    def shutdown(self):
        self._server.shutdown(); self._server.server_close()


def _token_path(cfg: Config, provider: str) -> Path:
    d = cfg.state_dir / "oauth"; d.mkdir(parents=True, exist_ok=True)
    return d / f"{provider}.json"

def save_token(cfg: Config, provider: str, token: dict[str, Any]) -> None:
    p = _token_path(cfg, provider)
    p.write_text(json.dumps(token))
    p.chmod(0o600)

def load_token(cfg: Config, provider: str) -> dict[str, Any] | None:
    p = _token_path(cfg, provider)
    return json.loads(p.read_text()) if p.exists() else None

def refresh_if_needed(
    cfg: Config, provider: str,
    token_url: str, client_id: str, client_secret: str,
) -> dict[str, Any]:
    """Refresh the token if expired. Returns the (possibly refreshed) token."""
    token = load_token(cfg, provider) or {}
    if token.get("expires_at", 0) > time.time() + 60:
        return token
    if "refresh_token" not in token:
        raise RuntimeError(f"{provider}: no refresh_token; re-run setup")
    r = requests.post(token_url, data={
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
        "client_id": client_id, "client_secret": client_secret,
    }, timeout=10)
    r.raise_for_status()
    new_token = r.json()
    new_token["expires_at"] = int(time.time()) + int(new_token.get("expires_in", 3600))
    if "refresh_token" not in new_token:
        new_token["refresh_token"] = token["refresh_token"]
    save_token(cfg, provider, new_token)
    return new_token
```

- [ ] **Step 4: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(oauth): localhost callback flow + token storage + refresh"
```

---

### Task 16: Notes module

**Files:**
- Create: `src/personal_db/notes.py`, `tests/unit/test_notes.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_notes.py`:
```python
from personal_db.config import Config
from personal_db.db import init_db, connect
from personal_db.notes import write_note, list_notes, read_note

def test_write_and_list_note(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    rel = write_note(cfg, title="weekly", body="# Weekly\nstuff")
    notes = list_notes(cfg)
    assert any(n["path"] == rel for n in notes)
    assert read_note(cfg, rel).startswith("# Weekly")
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/notes.py`**

```python
from __future__ import annotations
import re
from datetime import date
from pathlib import Path
from personal_db.config import Config
from personal_db.db import connect

_SLUG_RE = re.compile(r"[^a-z0-9]+")

def _slugify(s: str) -> str:
    return _SLUG_RE.sub("-", s.lower()).strip("-") or "note"

def write_note(cfg: Config, title: str, body: str) -> str:
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{date.today().isoformat()}-{_slugify(title)}.md"
    p = cfg.notes_dir / fname
    p.write_text(body)
    excerpt = body.strip().splitlines()[0][:200] if body.strip() else ""
    con = connect(cfg.db_path)
    con.execute(
        "INSERT INTO notes(path,title,created_at,body_excerpt) VALUES (?,?,datetime('now'),?) "
        "ON CONFLICT(path) DO UPDATE SET title=excluded.title, body_excerpt=excluded.body_excerpt",
        (fname, title, excerpt),
    )
    con.commit(); con.close()
    return fname

def list_notes(cfg: Config, query: str | None = None) -> list[dict]:
    con = connect(cfg.db_path, read_only=True)
    if query:
        rows = con.execute(
            "SELECT path,title,created_at,body_excerpt FROM notes "
            "WHERE title LIKE ? OR body_excerpt LIKE ? ORDER BY created_at DESC",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT path,title,created_at,body_excerpt FROM notes ORDER BY created_at DESC"
        ).fetchall()
    con.close()
    return [{"path": r[0], "title": r[1], "created_at": r[2], "excerpt": r[3]} for r in rows]

def read_note(cfg: Config, rel_path: str) -> str:
    return (cfg.notes_dir / rel_path).read_text()
```

- [ ] **Step 4: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(notes): write/list/read notes with SQLite index"
```

---

### Task 17: MCP server tools (functions only — server in next task)

**Files:**
- Create: `src/personal_db/mcp_server/__init__.py`, `src/personal_db/mcp_server/tools.py`, `tests/unit/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_mcp_tools.py`:
```python
import yaml
from personal_db.config import Config
from personal_db.db import init_db, apply_tracker_schema, connect
from personal_db.mcp_server.tools import (
    list_trackers, describe_tracker, query, get_series,
    list_entities, log_event_tool, list_notes_tool, read_note_tool,
)

def _make_demo(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / "demo"; d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "name": "demo", "description": "demo tracker", "permission_type": "none",
        "setup_steps": [], "schedule": {"every": "1h"},
        "time_column": "ts", "granularity": "event",
        "schema": {"tables": {"demo": {"columns": {
            "id": {"type": "TEXT", "semantic": "id"},
            "ts": {"type": "TEXT", "semantic": "iso ts"},
            "value": {"type": "INTEGER", "semantic": "v"},
        }}}},
    }))
    apply_tracker_schema(cfg.db_path,
        "CREATE TABLE demo (id TEXT PRIMARY KEY, ts TEXT, value INTEGER);")
    con = connect(cfg.db_path)
    con.executemany("INSERT INTO demo VALUES (?,?,?)",
        [("a", "2026-04-25T08:00", 1), ("b", "2026-04-25T09:00", 2),
         ("c", "2026-04-26T08:00", 3)])
    con.commit(); con.close()
    return cfg

def test_list_trackers(tmp_root):
    cfg = _make_demo(tmp_root)
    out = list_trackers(cfg)
    assert any(t["name"] == "demo" for t in out)

def test_describe_tracker(tmp_root):
    cfg = _make_demo(tmp_root)
    out = describe_tracker(cfg, "demo")
    assert out["time_column"] == "ts"
    assert "demo" in out["schema"]["tables"]

def test_query_select_only(tmp_root):
    cfg = _make_demo(tmp_root)
    rows = query(cfg, "SELECT id FROM demo ORDER BY id")
    assert [r["id"] for r in rows] == ["a", "b", "c"]

def test_query_rejects_writes(tmp_root):
    cfg = _make_demo(tmp_root)
    import pytest
    with pytest.raises(ValueError):
        query(cfg, "INSERT INTO demo VALUES ('x', 'now', 9)")
    with pytest.raises(ValueError):
        query(cfg, "SELECT 1; DROP TABLE demo")

def test_get_series_groups_by_day(tmp_root):
    cfg = _make_demo(tmp_root)
    rows = get_series(cfg, tracker="demo", range_="2026-04-25/2026-04-26",
                      granularity="day", agg="sum", value_column="value")
    by_day = {r["bucket"]: r["value"] for r in rows}
    assert by_day["2026-04-25"] == 3
    assert by_day["2026-04-26"] == 3

def test_log_event_tool(tmp_root):
    cfg = _make_demo(tmp_root)
    rid = log_event_tool(cfg, "demo", {"id": "z", "ts": "2026-04-27", "value": 5})
    assert rid is not None
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement `src/personal_db/mcp_server/tools.py`**

```python
from __future__ import annotations
import re
from typing import Any
from personal_db.config import Config
from personal_db.db import connect
from personal_db.manifest import load_manifest
from personal_db.log_event import log_event
from personal_db.notes import list_notes, read_note

# Reject any write or schema-altering verb. Also block `;` to prevent stacked statements.
_WRITE_VERBS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM)\b",
    re.IGNORECASE,
)

def _validate_select(sql: str) -> None:
    if ";" in sql.rstrip(";").strip(";"):
        raise ValueError("multiple statements not allowed")
    if _WRITE_VERBS_RE.search(sql):
        raise ValueError("only SELECT queries allowed")
    head = sql.lstrip().lstrip("(").lstrip().upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("query must start with SELECT or WITH")

def list_trackers(cfg: Config) -> list[dict[str, str]]:
    if not cfg.trackers_dir.exists(): return []
    out = []
    for d in sorted(cfg.trackers_dir.iterdir()):
        m = d / "manifest.yaml"
        if d.is_dir() and m.exists():
            man = load_manifest(m)
            out.append({"name": man.name, "description": man.description})
    return out

def describe_tracker(cfg: Config, name: str) -> dict[str, Any]:
    return load_manifest(cfg.trackers_dir / name / "manifest.yaml").model_dump()

def query(cfg: Config, sql: str, params: list | None = None) -> list[dict[str, Any]]:
    _validate_select(sql)
    con = connect(cfg.db_path, read_only=True)
    cur = con.execute(sql, params or [])
    cols = [c[0] for c in cur.description] if cur.description else []
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    con.close()
    return rows

def get_series(
    cfg: Config, *, tracker: str, range_: str,
    granularity: str = "day", agg: str = "sum", value_column: str | None = None,
) -> list[dict[str, Any]]:
    """Time-bucketed series. range_ is 'YYYY-MM-DD/YYYY-MM-DD'.
    Uses a matching `views/<tracker>_<granularity>.sql` view if present, else GROUP BY."""
    manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
    table = tracker if tracker in manifest.schema.tables else next(iter(manifest.schema.tables))
    time_col = manifest.time_column
    start, end = range_.split("/")
    if granularity not in ("hour", "day", "week", "month"):
        raise ValueError(f"unsupported granularity: {granularity}")
    if agg not in ("sum", "avg", "count", "min", "max"):
        raise ValueError(f"unsupported agg: {agg}")
    expr_value = f"{agg}({value_column})" if value_column else "count(*)"
    fmt = {"hour": "%Y-%m-%dT%H", "day": "%Y-%m-%d",
           "week": "%Y-W%W", "month": "%Y-%m"}[granularity]
    sql = (
        f"SELECT strftime('{fmt}', {time_col}) AS bucket, {expr_value} AS value "
        f"FROM {table} WHERE {time_col} >= ? AND {time_col} < ? "
        f"GROUP BY bucket ORDER BY bucket"
    )
    return query(cfg, sql, params=[start, end])

def list_entities(cfg: Config, kind: str, query_str: str | None = None) -> list[dict[str, Any]]:
    if kind not in ("people", "topics"):
        raise ValueError("kind must be 'people' or 'topics'")
    id_col = "person_id" if kind == "people" else "topic_id"
    con = connect(cfg.db_path, read_only=True)
    if query_str:
        sql = (f"SELECT e.{id_col} as id, e.display_name, "
               f"GROUP_CONCAT(a.alias) as aliases "
               f"FROM {kind} e LEFT JOIN {kind}_aliases a USING({id_col}) "
               f"WHERE e.display_name LIKE ? OR a.alias LIKE ? "
               f"GROUP BY e.{id_col}")
        rows = con.execute(sql, (f"%{query_str}%", f"%{query_str}%")).fetchall()
    else:
        sql = (f"SELECT e.{id_col} as id, e.display_name, "
               f"GROUP_CONCAT(a.alias) as aliases "
               f"FROM {kind} e LEFT JOIN {kind}_aliases a USING({id_col}) "
               f"GROUP BY e.{id_col}")
        rows = con.execute(sql).fetchall()
    con.close()
    return [{"id": r[0], "display_name": r[1],
             "aliases": (r[2].split(",") if r[2] else [])} for r in rows]

def log_event_tool(cfg: Config, tracker: str, fields: dict) -> int:
    return log_event(cfg, tracker, fields)

def list_notes_tool(cfg: Config, query_str: str | None = None) -> list[dict]:
    return list_notes(cfg, query_str)

def read_note_tool(cfg: Config, path: str) -> str:
    return read_note(cfg, path)
```

- [ ] **Step 4: Run, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(mcp): tool functions — list/describe/query/get_series/entities/log/notes"
```

---

### Task 18: MCP server bootstrap + `personal-db mcp`

**Files:**
- Create: `src/personal_db/mcp_server/server.py`, `src/personal_db/cli/mcp_cmd.py`, `tests/integration/test_mcp_server.py`
- Modify: `src/personal_db/cli/main.py`

- [ ] **Step 1: Write failing test (subprocess speaks MCP over stdio)**

`tests/integration/test_mcp_server.py`:
```python
import json, subprocess, sys, time, yaml
from pathlib import Path

def test_mcp_server_handles_list_trackers(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "init"], check=True, capture_output=True)
    # Make a demo tracker so list_trackers has something to return
    d = root / "trackers" / "demo"; d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "name": "demo", "description": "x", "permission_type": "none",
        "setup_steps": [], "schedule": {"every": "1h"},
        "time_column": "ts", "granularity": "event",
        "schema": {"tables": {"demo": {"columns": {
            "ts": {"type": "TEXT", "semantic": "ts"}}}}},
    }))
    proc = subprocess.Popen(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        # MCP initialize handshake
        init_req = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
        }
        proc.stdin.write((json.dumps(init_req) + "\n").encode()); proc.stdin.flush()
        # Read the init response (one JSON-RPC line)
        line = proc.stdout.readline()
        assert line, "no init response"
        resp = json.loads(line)
        assert resp["id"] == 1
        # Notification: initialized
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        proc.stdin.write((json.dumps(notif) + "\n").encode()); proc.stdin.flush()
        # tools/call list_trackers
        call_req = {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "list_trackers", "arguments": {}},
        }
        proc.stdin.write((json.dumps(call_req) + "\n").encode()); proc.stdin.flush()
        line = proc.stdout.readline()
        resp = json.loads(line)
        # Tool result content is a list of TextContent items per MCP spec
        text = resp["result"]["content"][0]["text"]
        data = json.loads(text)
        assert any(t["name"] == "demo" for t in data)
    finally:
        proc.terminate(); proc.wait(timeout=3)
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Implement the server using `mcp` SDK**

`src/personal_db/mcp_server/server.py`:
```python
from __future__ import annotations
import json
from pathlib import Path
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from personal_db.config import Config
from personal_db.mcp_server import tools as T

def build_server(cfg: Config) -> Server:
    server = Server("personal_db")

    @server.list_tools()
    async def _list() -> list[Tool]:
        return [
            Tool(name="list_trackers", description="List installed trackers + descriptions",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="describe_tracker", description="Get full manifest for a tracker",
                 inputSchema={"type": "object",
                              "properties": {"name": {"type": "string"}},
                              "required": ["name"]}),
            Tool(name="query", description="Run read-only SQL (SELECT/WITH only) against db.sqlite",
                 inputSchema={"type": "object",
                              "properties": {"sql": {"type": "string"},
                                             "params": {"type": "array",
                                                        "items": {"type": ["string","number","null"]}}},
                              "required": ["sql"]}),
            Tool(name="get_series", description="Bucketed time-series for a tracker",
                 inputSchema={"type": "object",
                              "properties": {
                                  "tracker": {"type": "string"},
                                  "range": {"type": "string", "description": "YYYY-MM-DD/YYYY-MM-DD"},
                                  "granularity": {"type": "string",
                                                  "enum": ["hour","day","week","month"]},
                                  "agg": {"type": "string",
                                          "enum": ["sum","avg","count","min","max"]},
                                  "value_column": {"type": "string"},
                              },
                              "required": ["tracker", "range"]}),
            Tool(name="list_entities", description="List people or topics with aliases",
                 inputSchema={"type": "object",
                              "properties": {
                                  "kind": {"type": "string", "enum": ["people","topics"]},
                                  "query": {"type": "string"}},
                              "required": ["kind"]}),
            Tool(name="log_event", description="Insert a row into a tracker (manual capture)",
                 inputSchema={"type": "object",
                              "properties": {
                                  "tracker": {"type": "string"},
                                  "fields": {"type": "object"}},
                              "required": ["tracker", "fields"]}),
            Tool(name="list_notes", description="List previously written analysis notes",
                 inputSchema={"type": "object",
                              "properties": {"query": {"type": "string"}}}),
            Tool(name="read_note", description="Read a note by its relative path",
                 inputSchema={"type": "object",
                              "properties": {"path": {"type": "string"}},
                              "required": ["path"]}),
        ]

    @server.call_tool()
    async def _call(name: str, arguments: dict) -> list[TextContent]:
        if name == "list_trackers": result = T.list_trackers(cfg)
        elif name == "describe_tracker": result = T.describe_tracker(cfg, arguments["name"])
        elif name == "query":
            result = T.query(cfg, arguments["sql"], arguments.get("params"))
        elif name == "get_series":
            result = T.get_series(
                cfg,
                tracker=arguments["tracker"],
                range_=arguments["range"],
                granularity=arguments.get("granularity", "day"),
                agg=arguments.get("agg", "sum"),
                value_column=arguments.get("value_column"),
            )
        elif name == "list_entities":
            result = T.list_entities(cfg, arguments["kind"], arguments.get("query"))
        elif name == "log_event":
            result = {"rowid": T.log_event_tool(cfg, arguments["tracker"], arguments["fields"])}
        elif name == "list_notes":
            result = T.list_notes_tool(cfg, arguments.get("query"))
        elif name == "read_note":
            result = T.read_note_tool(cfg, arguments["path"])
        else:
            raise ValueError(f"unknown tool {name}")
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    return server

async def run(cfg: Config) -> None:
    server = build_server(cfg)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
```

`src/personal_db/cli/mcp_cmd.py`:
```python
import asyncio
from personal_db.cli import main as cli_main
from personal_db.config import Config
from personal_db.mcp_server.server import run as run_server

def mcp() -> None:
    """Run the MCP stdio server (called by Claude Code)."""
    cfg = Config(root=cli_main.get_root())
    asyncio.run(run_server(cfg))
```

In `main.py`:
```python
from personal_db.cli import mcp_cmd
app.command("mcp")(mcp_cmd.mcp)
```

- [ ] **Step 4: Run integration test, pass. Lint. Commit.**

```bash
pytest tests/integration/test_mcp_server.py -v
git add -A
git commit -m "feat(mcp): stdio server bootstrap + 8 tools wired + personal-db mcp"
```

---

### Task 19: Connector — `github_commits`

**Files:**
- Create: `src/personal_db/templates/trackers/github_commits/manifest.yaml`, `schema.sql`, `ingest.py`, `__init__.py`
- Create: `tests/fixtures/github/commits_page1.json`
- Create: `tests/integration/test_connector_github.py`

- [ ] **Step 1: Write fixture and test**

`tests/fixtures/github/commits_page1.json`: a small JSON array of 2-3 sample commits in the GitHub `/users/:user/events/public` shape (reduce to fields the ingest uses: `sha`, `repo.name`, `commit.author.date`, `commit.message`, `stats.additions`, `stats.deletions`). Keep it under 30 lines.

`tests/integration/test_connector_github.py`:
```python
import json, subprocess, sys, yaml
from pathlib import Path
from unittest.mock import patch

def test_github_sync_inserts_rows_from_fixture(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "init"], check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "tracker", "install", "github_commits"],
                   check=True, capture_output=True)
    fixture = json.loads(Path("tests/fixtures/github/commits_page1.json").read_text())
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("GITHUB_USER", "octocat")
    # Patch requests.get used inside ingest
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = fixture
        mock_get.return_value.headers = {"Link": ""}  # no next page
        r = subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                            "--root", str(root), "sync", "github_commits"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    import sqlite3
    con = sqlite3.connect(root / "db.sqlite")
    n = con.execute("SELECT COUNT(*) FROM github_commits").fetchone()[0]
    assert n == len(fixture)
```

(Note: the test patches `requests.get` in the ingest module's namespace; depending on how `ingest.py` imports it, you may need to patch `personal_db_trackers_github_commits.requests.get` instead. Adjust after first run.)

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Write the tracker template**

`src/personal_db/templates/trackers/github_commits/__init__.py`: empty.

`manifest.yaml`:
```yaml
name: github_commits
description: Commits authored across GitHub
permission_type: api_key
setup_steps:
  - "Set GITHUB_TOKEN env var with a personal access token (scopes: read:user, repo)"
  - "Set GITHUB_USER env var to your GitHub username"
schedule:
  every: 4h
time_column: committed_at
granularity: event
schema:
  tables:
    github_commits:
      columns:
        sha:          {type: TEXT,    semantic: "commit SHA, primary key"}
        repo:         {type: TEXT,    semantic: "owner/name"}
        committed_at: {type: TEXT,    semantic: "ISO-8601 commit timestamp (UTC)"}
        message:      {type: TEXT,    semantic: "first line of commit message"}
        additions:    {type: INTEGER, semantic: "lines added"}
        deletions:    {type: INTEGER, semantic: "lines deleted"}
related_entities: []
```

`schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS github_commits (
  sha          TEXT PRIMARY KEY,
  repo         TEXT,
  committed_at TEXT NOT NULL,
  message      TEXT,
  additions    INTEGER,
  deletions    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_github_commits_committed_at
  ON github_commits(committed_at);
```

`ingest.py`:
```python
import os
import requests
from personal_db.tracker import Tracker

API = "https://api.github.com"

def _flatten_event(ev: dict) -> list[dict]:
    rows = []
    if ev.get("type") != "PushEvent":
        return rows
    repo = ev.get("repo", {}).get("name", "")
    for c in ev.get("payload", {}).get("commits", []):
        rows.append({
            "sha": c["sha"],
            "repo": repo,
            "committed_at": ev["created_at"],  # event time as a proxy
            "message": (c.get("message") or "").splitlines()[0][:500],
            "additions": None,
            "deletions": None,
        })
    return rows

def _fetch(url: str, headers: dict) -> tuple[list[dict], str | None]:
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    next_link = None
    link_hdr = r.headers.get("Link", "")
    for part in link_hdr.split(","):
        if 'rel="next"' in part:
            next_link = part.split(";")[0].strip().lstrip("<").rstrip(">")
    return r.json(), next_link

def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)  # Events API only returns ~90 days; backfill == sync for v0

def sync(t: Tracker) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    user = os.environ.get("GITHUB_USER")
    if not token or not user:
        raise RuntimeError("Set GITHUB_TOKEN and GITHUB_USER env vars (see manifest setup_steps)")
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    url = f"{API}/users/{user}/events/public?per_page=100"
    cursor = t.cursor.get()
    all_rows: list[dict] = []
    while url:
        events, url = _fetch(url, headers)
        for ev in events:
            if cursor and ev["created_at"] <= cursor:
                url = None  # stop paginating; we've reached the cursor
                break
            all_rows.extend(_flatten_event(ev))
    if all_rows:
        t.upsert("github_commits", all_rows, key=["sha"])
        t.cursor.set(max(r["committed_at"] for r in all_rows))
    t.log.info("github_commits: ingested %d rows", len(all_rows))
```

- [ ] **Step 4: Run, debug, pass. Lint. Commit.**

```bash
git add -A
git commit -m "feat(connector): github_commits — events API ingest with cursor"
```

---

### Task 20: Connector — `whoop`

**Files:**
- Create: `src/personal_db/templates/trackers/whoop/{__init__.py,manifest.yaml,schema.sql,ingest.py}`
- Create: `tests/fixtures/whoop/cycles.json`, `tests/integration/test_connector_whoop.py`

- [ ] **Step 1: Write fixture (3 sample cycle records, key fields: `id`, `start`, `end`, `score.strain`, `score.average_heart_rate`)**

- [ ] **Step 2: Write failing test**

`tests/integration/test_connector_whoop.py`:
```python
import json, subprocess, sys
from pathlib import Path
from unittest.mock import patch
from personal_db.config import Config
from personal_db.oauth import save_token

def test_whoop_sync_inserts_cycles(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "init"], check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "tracker", "install", "whoop"],
                   check=True, capture_output=True)
    cfg = Config(root=root)
    save_token(cfg, "whoop", {
        "access_token": "x", "refresh_token": "r",
        "expires_at": 9999999999,
    })
    fixture = json.loads(Path("tests/fixtures/whoop/cycles.json").read_text())
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"records": fixture, "next_token": None}
        r = subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                            "--root", str(root), "sync", "whoop"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    import sqlite3
    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT COUNT(*) FROM whoop_cycles").fetchone()[0] == len(fixture)
```

- [ ] **Step 3: Write template**

`manifest.yaml`:
```yaml
name: whoop
description: Whoop daily cycles (strain, recovery, average HR)
permission_type: oauth
setup_steps:
  - "Run `personal-db tracker setup whoop` to launch OAuth flow"
  - "Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET env vars first"
schedule:
  every: 6h
time_column: start
granularity: day
schema:
  tables:
    whoop_cycles:
      columns:
        id:                {type: TEXT,    semantic: "Whoop cycle id"}
        start:             {type: TEXT,    semantic: "ISO-8601 cycle start (UTC)"}
        end:               {type: TEXT,    semantic: "ISO-8601 cycle end (UTC)"}
        strain:            {type: REAL,    semantic: "0-21 daily strain"}
        average_heart_rate:{type: INTEGER, semantic: "bpm avg"}
related_entities: []
```

`schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS whoop_cycles (
  id                 TEXT PRIMARY KEY,
  start              TEXT NOT NULL,
  end                TEXT,
  strain             REAL,
  average_heart_rate INTEGER
);
CREATE INDEX IF NOT EXISTS idx_whoop_cycles_start ON whoop_cycles(start);
```

`ingest.py`:
```python
import os, requests
from personal_db.tracker import Tracker
from personal_db.oauth import refresh_if_needed, load_token

WHOOP_API = "https://api.prod.whoop.com/developer"

def _client_credentials():
    cid = os.environ.get("WHOOP_CLIENT_ID")
    cs  = os.environ.get("WHOOP_CLIENT_SECRET")
    if not cid or not cs:
        raise RuntimeError("Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET")
    return cid, cs

def _flatten(rec: dict) -> dict:
    score = rec.get("score") or {}
    return {
        "id": str(rec["id"]),
        "start": rec["start"],
        "end": rec.get("end"),
        "strain": score.get("strain"),
        "average_heart_rate": score.get("average_heart_rate"),
    }

def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)

def sync(t: Tracker) -> None:
    cid, cs = _client_credentials()
    token = refresh_if_needed(t.cfg, "whoop",
                              token_url=f"{WHOOP_API}/oauth/oauth2/token",
                              client_id=cid, client_secret=cs)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    cursor = t.cursor.get()
    params = {"limit": 25}
    if cursor:
        params["start"] = cursor
    rows: list[dict] = []
    next_token = None
    while True:
        if next_token: params["nextToken"] = next_token
        r = requests.get(f"{WHOOP_API}/v1/cycle", headers=headers, params=params, timeout=15)
        r.raise_for_status()
        body = r.json()
        rows.extend(_flatten(rec) for rec in body.get("records", []))
        next_token = body.get("next_token")
        if not next_token: break
    if rows:
        t.upsert("whoop_cycles", rows, key=["id"])
        t.cursor.set(max(r["start"] for r in rows))
    t.log.info("whoop: ingested %d cycles", len(rows))
```

- [ ] **Step 4: Run, debug, pass. Commit.**

```bash
git add -A
git commit -m "feat(connector): whoop cycles via OAuth + refresh"
```

---

### Task 21: Connector — `screen_time`

**Files:**
- Create: `src/personal_db/templates/trackers/screen_time/{__init__.py,manifest.yaml,schema.sql,ingest.py}`
- Create: `tests/fixtures/screen_time/knowledgeC_mini.sqlite` (a hand-built SQLite file with a few rows of `ZOBJECT` mimicking the relevant schema; checked in as binary)
- Create: `tests/integration/test_connector_screen_time.py`

- [ ] **Step 1: Build the fixture SQLite**

In a one-off Python REPL run (this is for the *engineer* doing the task, not the test):
```python
import sqlite3
con = sqlite3.connect("tests/fixtures/screen_time/knowledgeC_mini.sqlite")
con.executescript("""
CREATE TABLE ZOBJECT (
  Z_PK INTEGER PRIMARY KEY,
  ZSTREAMNAME TEXT,
  ZVALUESTRING TEXT,
  ZSTARTDATE REAL,
  ZENDDATE REAL,
  ZSECONDSFROMGMT REAL
);
INSERT INTO ZOBJECT (ZSTREAMNAME, ZVALUESTRING, ZSTARTDATE, ZENDDATE) VALUES
  ('/app/usage', 'com.tinyspeck.slackmacgap', 768657600.0, 768658200.0),
  ('/app/usage', 'com.apple.dt.Xcode',        768658200.0, 768659400.0),
  ('/app/usage', 'com.tinyspeck.slackmacgap', 768659400.0, 768659700.0);
""")
con.commit(); con.close()
```
(`ZSTARTDATE` is Apple Cocoa epoch seconds — seconds since 2001-01-01 00:00:00 UTC.)

- [ ] **Step 2: Write failing test**

`tests/integration/test_connector_screen_time.py`:
```python
import shutil, subprocess, sys
from pathlib import Path

def test_screen_time_sync_reads_fixture_db(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "init"], check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "tracker", "install", "screen_time"],
                   check=True, capture_output=True)
    fixture = Path("tests/fixtures/screen_time/knowledgeC_mini.sqlite")
    monkeypatch.setenv("PERSONAL_DB_SCREEN_TIME_DB", str(fixture))
    r = subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                        "--root", str(root), "sync", "screen_time"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    import sqlite3
    con = sqlite3.connect(root / "db.sqlite")
    n = con.execute("SELECT COUNT(*) FROM screen_time_app_usage").fetchone()[0]
    assert n == 3
```

- [ ] **Step 3: Write template**

`manifest.yaml`:
```yaml
name: screen_time
description: macOS app usage from ~/Library/Application Support/Knowledge/knowledgeC.db
permission_type: full_disk_access
setup_steps:
  - "Run `personal-db permission check screen_time` to verify FDA"
  - "If denied, grant Full Disk Access to your terminal binary in System Settings"
schedule:
  every: 1h
time_column: start_at
granularity: event
schema:
  tables:
    screen_time_app_usage:
      columns:
        id:         {type: INTEGER, semantic: "rowid"}
        bundle_id:  {type: TEXT,    semantic: "macOS bundle identifier (e.g. com.apple.dt.Xcode)"}
        start_at:   {type: TEXT,    semantic: "ISO-8601 UTC"}
        end_at:     {type: TEXT,    semantic: "ISO-8601 UTC"}
        seconds:    {type: INTEGER, semantic: "duration in seconds"}
related_entities: []
```

`schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS screen_time_app_usage (
  id        INTEGER PRIMARY KEY,
  bundle_id TEXT NOT NULL,
  start_at  TEXT NOT NULL,
  end_at    TEXT NOT NULL,
  seconds   INTEGER NOT NULL,
  UNIQUE(bundle_id, start_at)
);
CREATE INDEX IF NOT EXISTS idx_screen_time_start ON screen_time_app_usage(start_at);
```

`ingest.py`:
```python
import os, sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from personal_db.tracker import Tracker

# Apple Cocoa epoch: 2001-01-01 UTC
COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
DEFAULT_DB = Path("~/Library/Application Support/Knowledge/knowledgeC.db").expanduser()

def _cocoa_to_iso(seconds: float) -> str:
    return (COCOA_EPOCH + timedelta(seconds=seconds)).isoformat()

def _resolve_db_path() -> Path:
    return Path(os.environ.get("PERSONAL_DB_SCREEN_TIME_DB", str(DEFAULT_DB)))

def backfill(t: Tracker, start, end) -> None:
    sync(t)  # full read; UNIQUE constraint handles dedup

def sync(t: Tracker) -> None:
    db = _resolve_db_path()
    if not db.exists():
        raise RuntimeError(f"knowledgeC.db not found at {db}")
    cursor_iso = t.cursor.get(default="2020-01-01T00:00:00+00:00")
    # ZSTARTDATE is Cocoa-epoch seconds. Convert cursor back to compare.
    cursor_dt = datetime.fromisoformat(cursor_iso)
    cursor_cocoa = (cursor_dt - COCOA_EPOCH).total_seconds()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows_in = con.execute("""
        SELECT ZVALUESTRING, ZSTARTDATE, ZENDDATE
        FROM ZOBJECT
        WHERE ZSTREAMNAME='/app/usage' AND ZSTARTDATE > ?
        ORDER BY ZSTARTDATE
    """, (cursor_cocoa,)).fetchall()
    con.close()
    rows = []
    for bundle, s, e in rows_in:
        if not bundle or s is None or e is None: continue
        rows.append({
            "bundle_id": bundle,
            "start_at": _cocoa_to_iso(s),
            "end_at": _cocoa_to_iso(e),
            "seconds": int(e - s),
        })
    if rows:
        t.upsert("screen_time_app_usage", rows, key=["bundle_id", "start_at"])
        t.cursor.set(rows[-1]["start_at"])
    t.log.info("screen_time: ingested %d rows", len(rows))
```

- [ ] **Step 4: Run, pass. Commit.**

```bash
git add -A
git commit -m "feat(connector): screen_time from knowledgeC.db (FDA gated)"
```

---

### Task 22: Connector — `imessage` (with entity resolution)

**Files:**
- Create: `src/personal_db/templates/trackers/imessage/{__init__.py,manifest.yaml,schema.sql,ingest.py}`
- Create: `tests/fixtures/imessage/chat_mini.sqlite`
- Create: `tests/integration/test_connector_imessage.py`

- [ ] **Step 1: Build a minimal `chat_mini.sqlite`**

Schema based on macOS chat.db essentials. Engineer should run once to materialize the file:
```python
import sqlite3
con = sqlite3.connect("tests/fixtures/imessage/chat_mini.sqlite")
con.executescript("""
CREATE TABLE handle (
  ROWID INTEGER PRIMARY KEY,
  id TEXT  -- phone or email
);
CREATE TABLE message (
  ROWID INTEGER PRIMARY KEY,
  handle_id INTEGER,
  text TEXT,
  is_from_me INTEGER,
  date INTEGER  -- Apple nanoseconds since 2001-01-01
);
INSERT INTO handle VALUES (1, 'marko@example.com'), (2, '+15551234567');
INSERT INTO message (handle_id, text, is_from_me, date) VALUES
  (1, 'hey',   0, 768657600000000000),
  (1, 'reply', 1, 768657660000000000),
  (2, 'yo',    0, 768658000000000000);
""")
con.commit(); con.close()
```

- [ ] **Step 2: Write failing test**

`tests/integration/test_connector_imessage.py`:
```python
import subprocess, sys, yaml
from pathlib import Path

def test_imessage_sync_resolves_people(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "init"], check=True, capture_output=True)
    # Pre-register Marko so the alias matches
    (root / "entities" / "people.yaml").write_text(yaml.safe_dump([
        {"display_name": "Marko Chen",
         "aliases": ["marko@example.com", "+15551234567"]},
    ]))
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "tracker", "install", "imessage"],
                   check=True, capture_output=True)
    monkeypatch.setenv("PERSONAL_DB_IMESSAGE_DB", "tests/fixtures/imessage/chat_mini.sqlite")
    r = subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                        "--root", str(root), "sync", "imessage"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    import sqlite3
    con = sqlite3.connect(root / "db.sqlite")
    rows = con.execute(
        "SELECT person_id, COUNT(*) FROM imessage_messages "
        "WHERE person_id IS NOT NULL GROUP BY person_id"
    ).fetchall()
    # All 3 messages should resolve to the single Marko person_id
    assert len(rows) == 1 and rows[0][1] == 3
```

- [ ] **Step 3: Write template**

`manifest.yaml`:
```yaml
name: imessage
description: iMessage messages from ~/Library/Messages/chat.db
permission_type: full_disk_access
setup_steps:
  - "Run `personal-db permission check imessage`"
  - "Add aliases (emails/phones) for known people in entities/people.yaml"
schedule:
  every: 30m
time_column: sent_at
granularity: event
schema:
  tables:
    imessage_messages:
      columns:
        id:          {type: INTEGER, semantic: "rowid in chat.db"}
        person_id:   {type: INTEGER, semantic: "FK -> people.person_id (nullable if unresolved)"}
        handle:      {type: TEXT,    semantic: "raw phone or email"}
        text:        {type: TEXT,    semantic: "message body"}
        is_from_me:  {type: INTEGER, semantic: "1 if I sent it"}
        sent_at:     {type: TEXT,    semantic: "ISO-8601 UTC"}
related_entities: ["people"]
```

`schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS imessage_messages (
  id          INTEGER PRIMARY KEY,
  person_id   INTEGER REFERENCES people(person_id),
  handle      TEXT,
  text        TEXT,
  is_from_me  INTEGER NOT NULL,
  sent_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_imessage_sent ON imessage_messages(sent_at);
CREATE INDEX IF NOT EXISTS idx_imessage_person ON imessage_messages(person_id);
```

`ingest.py`:
```python
import os, sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from personal_db.tracker import Tracker
from personal_db.entities import sync_entities_from_yaml

COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
DEFAULT_DB = Path("~/Library/Messages/chat.db").expanduser()

def _ns_to_iso(ns: int) -> str:
    # macOS sometimes stores seconds, sometimes nanoseconds; sniff scale
    if ns > 10**14:
        seconds = ns / 1e9
    else:
        seconds = float(ns)
    return (COCOA_EPOCH + timedelta(seconds=seconds)).isoformat()

def _resolve_db() -> Path:
    return Path(os.environ.get("PERSONAL_DB_IMESSAGE_DB", str(DEFAULT_DB)))

def backfill(t: Tracker, start, end) -> None:
    sync(t)

def sync(t: Tracker) -> None:
    sync_entities_from_yaml(t.cfg)  # ensure aliases are loaded
    db = _resolve_db()
    if not db.exists():
        raise RuntimeError(f"chat.db not found at {db}")
    last = t.cursor.get(default="0")  # cursor is the max ROWID seen
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows_in = con.execute("""
        SELECT m.ROWID, h.id, m.text, m.is_from_me, m.date
        FROM message m LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE m.ROWID > ?
        ORDER BY m.ROWID
    """, (int(last),)).fetchall()
    con.close()
    rows = []
    for rowid, handle, text, is_from_me, date in rows_in:
        person_id = t.resolve_person(handle, auto_create=True) if handle else None
        rows.append({
            "id": rowid,
            "person_id": person_id,
            "handle": handle,
            "text": text or "",
            "is_from_me": int(is_from_me or 0),
            "sent_at": _ns_to_iso(date),
        })
    if rows:
        t.upsert("imessage_messages", rows, key=["id"])
        t.cursor.set(str(rows[-1]["id"]))
    t.log.info("imessage: ingested %d rows", len(rows))
```

- [ ] **Step 4: Run, pass. Commit.**

```bash
git add -A
git commit -m "feat(connector): imessage from chat.db with people-registry resolution"
```

---

### Task 23: Connector — `habits` (manual)

**Files:**
- Create: `src/personal_db/templates/trackers/habits/{__init__.py,manifest.yaml,schema.sql,ingest.py}`
- Create: `tests/integration/test_connector_habits.py`

- [ ] **Step 1: Write failing test**

`tests/integration/test_connector_habits.py`:
```python
import subprocess, sys

def test_habits_log_via_cli(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "init"], check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "tracker", "install", "habits"],
                   check=True, capture_output=True)
    # Schema must be applied — sync once with a no-op ingest does that
    subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                    "--root", str(root), "sync", "habits"],
                   check=True, capture_output=True)
    r = subprocess.run([sys.executable, "-m", "personal_db.cli.main",
                        "--root", str(root), "log", "habits",
                        "name=meditate", "value=1", "ts=2026-04-25T08:00"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    import sqlite3
    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT name,value FROM habits").fetchone() == ("meditate", "1")
```

- [ ] **Step 2: Run, fail.**

- [ ] **Step 3: Write template**

`manifest.yaml`:
```yaml
name: habits
description: Manually-logged daily habits (meditate, gym, read, etc.)
permission_type: manual
setup_steps:
  - "Log via the MCP tool log_event or `personal-db log habits name=… value=… ts=…`"
schedule: null  # never auto-syncs
time_column: ts
granularity: event
schema:
  tables:
    habits:
      columns:
        id:    {type: INTEGER, semantic: "rowid"}
        name:  {type: TEXT,    semantic: "habit name (free text)"}
        value: {type: TEXT,    semantic: "value as string (e.g. '1', '0', '15min')"}
        ts:    {type: TEXT,    semantic: "ISO-8601 event time"}
related_entities: []
```

`schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS habits (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  name  TEXT NOT NULL,
  value TEXT,
  ts    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_habits_name_ts ON habits(name, ts);
```

`ingest.py`:
```python
from personal_db.tracker import Tracker

def backfill(t: Tracker, start, end) -> None:
    pass  # no external source

def sync(t: Tracker) -> None:
    pass  # logged manually via MCP/CLI
```

- [ ] **Step 4: Adjust `_is_due` to handle `schedule: null`**

In `src/personal_db/sync.py`, `_is_due` already returns `False` when there's no schedule (`if not manifest.schedule or not manifest.schedule.every`). Confirm habits is correctly skipped in `sync_due`.

- [ ] **Step 5: Run, pass. Commit.**

```bash
git add -A
git commit -m "feat(connector): habits manual tracker"
```

---

### Task 24: Claude Code `/insights` skill

**Files:**
- Create: `src/personal_db/templates/claude_skill/insights.md`

- [ ] **Step 1: Write the skill file**

`src/personal_db/templates/claude_skill/insights.md`:
````markdown
---
name: personal-db-insights
description: Generate analysis of personal_db tracker data and write it to notes/. Use when the user asks for insights, patterns, weekly review, correlations, or comparisons across tracked metrics.
---

# Personal DB — Insights

You have access to the user's personal_db via MCP tools. Use them to answer questions about their data and write a markdown analysis.

## Available MCP tools (server `personal_db`)

- `list_trackers()` — see what's tracked
- `describe_tracker(name)` — get the schema/manifest before querying
- `query(sql)` — read-only SQL against `db.sqlite`
- `get_series(tracker, range, granularity?, agg?, value_column?)` — bucketed time series
- `list_entities(kind, query?)` — people/topics
- `log_event(tracker, fields)` — only when explicitly asked to log
- `list_notes(query?) / read_note(path)` — prior analyses

## Workflow for `/insights <question>`

1. Call `list_trackers` to see what data is available.
2. For each tracker that might be relevant, call `describe_tracker` to learn the schema. Don't guess columns.
3. Write `query` or `get_series` calls to fetch the data you need. Prefer `get_series` for time-bucketed comparisons.
4. Reason from the data. Note ambiguity. State sample sizes.
5. Use `log_event` only if the user asked to log something.
6. Write the analysis as a markdown file under the personal_db root's `notes/` directory using the `Write` tool. Filename convention: `notes/<YYYY-MM-DD>-<short-slug>.md`. The MCP `list_notes` tool auto-indexes any `.md` files it finds in `notes/`, so no extra registration step is needed.

## Output format for the note

```markdown
# <Topic> — <Date>

**Question:** <restate user question>

**Data sources:** <trackers used + date range>

## Findings

- <bullet>
- <bullet>

## Caveats

- <small sample size? confounders? data quality issues?>

## Charts (optional)

(ASCII sparklines or markdown tables only — no SVG/HTML in v0.)
```

## Style rules

- Be honest about uncertainty. Three weeks of data is not enough for "you sleep worse on Tuesdays."
- Show the SQL or `get_series` call you used. The user is technical and will want to verify.
- Never fabricate numbers. If the data isn't there, say so.
````

- [ ] **Step 2: Note: write_note in notes.py inserts the SQLite index entry. The slash command guidance above instructs Claude to use `Write`. Add a follow-up to make this consistent.**

Update `src/personal_db/notes.py` `list_notes()` to scan the `notes/` directory and insert any `.md` files not yet in the index, before querying. Replace `list_notes`:

```python
def list_notes(cfg: Config, query: str | None = None) -> list[dict]:
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    con = connect(cfg.db_path)
    indexed = {r[0] for r in con.execute("SELECT path FROM notes")}
    for f in cfg.notes_dir.glob("*.md"):
        if f.name in indexed: continue
        body = f.read_text()
        excerpt = body.strip().splitlines()[0][:200] if body.strip() else ""
        con.execute(
            "INSERT INTO notes(path,title,created_at,body_excerpt) VALUES (?,?,datetime('now'),?)",
            (f.name, f.stem, excerpt),
        )
    con.commit()
    if query:
        rows = con.execute(
            "SELECT path,title,created_at,body_excerpt FROM notes "
            "WHERE title LIKE ? OR body_excerpt LIKE ? ORDER BY created_at DESC",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT path,title,created_at,body_excerpt FROM notes ORDER BY created_at DESC"
        ).fetchall()
    con.close()
    return [{"path": r[0], "title": r[1], "created_at": r[2], "excerpt": r[3]} for r in rows]
```

- [ ] **Step 3: Update test in `tests/unit/test_notes.py`**

Add:
```python
def test_list_notes_picks_up_unindexed_files(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    (cfg.notes_dir / "2026-04-01-foo.md").write_text("# foo\nbar")
    notes = list_notes(cfg)
    assert any(n["title"] == "2026-04-01-foo" for n in notes)
```

Run the test, pass.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(claude-skill): /insights skill + notes auto-index from disk"
```

---

### Task 25: End-to-end smoke + README walk-through

**Files:**
- Create: `tests/integration/test_e2e_smoke.py`, `README.md` (rewrite)

- [ ] **Step 1: Write the smoke test**

`tests/integration/test_e2e_smoke.py`:
```python
import json, subprocess, sys, sqlite3, yaml
from pathlib import Path

def _run(*args, root=None):
    cmd = [sys.executable, "-m", "personal_db.cli.main"]
    if root: cmd += ["--root", str(root)]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"{' '.join(cmd)}\nSTDOUT:{r.stdout}\nSTDERR:{r.stderr}"
    return r

def test_e2e_init_install_log_query(tmp_path):
    root = tmp_path / "personal_db"
    _run("--root", str(root), "init")
    _run(root=root, *["tracker", "install", "habits"])
    _run(root=root, *["sync", "habits"])  # applies schema
    _run(root=root, *["log", "habits", "name=meditate", "value=1", "ts=2026-04-25T08:00"])
    _run(root=root, *["log", "habits", "name=meditate", "value=1", "ts=2026-04-26T08:00"])
    # Trackers list shows habits
    r = _run(root=root, *["tracker", "list"])
    assert "habits" in r.stdout
    # Direct SQL via MCP query path
    from personal_db.config import Config
    from personal_db.mcp_server.tools import query, get_series, list_trackers
    cfg = Config(root=root)
    rows = query(cfg, "SELECT name, COUNT(*) AS n FROM habits GROUP BY name")
    assert rows == [{"name": "meditate", "n": 2}]
    series = get_series(cfg, tracker="habits",
                       range_="2026-04-25/2026-04-27", granularity="day", agg="count")
    by_day = {r["bucket"]: r["value"] for r in series}
    assert by_day["2026-04-25"] == 1
    assert by_day["2026-04-26"] == 1
    # list_trackers
    assert any(t["name"] == "habits" for t in list_trackers(cfg))
```

- [ ] **Step 2: Run, debug, pass.**

- [ ] **Step 3: Rewrite README.md**

`README.md`:
```markdown
# personal_db

Personal data layer for AI agents. SQLite + per-tracker scripts + MCP server. macOS only in v0.

## Install

```bash
git clone <this repo>
cd personal_db
./scripts/install_dev.sh
source .venv/bin/activate
```

## Quick start

```bash
# Initialize the data root (default ~/personal_db)
personal-db init

# Install some built-in trackers
personal-db tracker install github_commits
personal-db tracker install whoop
personal-db tracker install screen_time
personal-db tracker install imessage
personal-db tracker install habits

# For each, run setup
export GITHUB_TOKEN=…  GITHUB_USER=…
export WHOOP_CLIENT_ID=…  WHOOP_CLIENT_SECRET=…
personal-db permission check screen_time   # opens System Settings if FDA missing
personal-db permission check imessage      # same

# First run: backfill what's available
personal-db backfill github_commits
personal-db backfill whoop

# Install the launchd scheduler (runs `personal-db sync --due` every 10 min)
personal-db scheduler install

# Add the MCP server to Claude Code
claude mcp add personal_db -- personal-db mcp

# Install the /insights skill
mkdir -p ~/.claude/skills/personal-db
cp src/personal_db/templates/claude_skill/insights.md ~/.claude/skills/personal-db/
```

## Verify

In Claude Code:
- "What trackers do I have?" → calls `list_trackers`
- "How many commits did I push last week?" → calls `query` or `get_series` against `github_commits`
- "Log that I meditated today" → calls `log_event("habits", …)`
- "/insights weekly review" → runs the skill, writes `notes/YYYY-MM-DD-weekly-review.md`

## Layout

See `docs/superpowers/specs/2026-04-25-personal-db-v0-design.md` for the full design.
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: end-to-end smoke + README walk-through"
```

---

## Self-Review (against spec)

After all 25 tasks complete, run this checklist before declaring v0 done:

**Spec coverage check** — every section of the design doc has a task:

| Spec § | Tasks |
|---|---|
| 3 Architecture | 1, 10, 14, 18 (no daemon, CLI + MCP + launchd) |
| 4 On-disk layout | 2, 3, 10 (config, tmp_root, init) |
| 5 Storage model | 4, 5, 6, 9 (db, manifest, tracker, schema apply) |
| 5.1 Entity registry | 4, 7, 8 (db core, entities module, Tracker.resolve_*) |
| 6 Connector contract | 6, 8, 9 (Tracker API, sync engine) |
| 7 CLI | 10, 11, 12, 13, 14, 18 |
| 8 MCP server | 17, 18 |
| 9 Manual entry | 12 (CLI), 17 (MCP log_event_tool), 23 (habits) |
| 10 Scheduling | 14 |
| 11 Claude Code integration | 24 (insights skill), 25 (README) |
| 12 v0 connectors | 19, 20, 21, 22, 23 |
| 14 Component boundaries | enforced by file structure (lock-in) |
| 15 Error handling | 9 (sync error isolation), 13 (permission), 15 (oauth refresh), 17 (query SELECT-only) |
| 16 Testing strategy | every task ships tests; fixtures live in `tests/fixtures/` |
| 17 Success criteria | 25 (e2e smoke + README) |

**Run the full test suite:**
```bash
pytest -v
```
Expected: all green except `live`-marked tests (which are opt-in).

**Manual smoke (10 minutes):**
1. `personal-db init` (in a fresh tmp dir)
2. `personal-db tracker install habits && personal-db sync habits`
3. `personal-db log habits name=meditate value=1 ts=2026-04-25T08:00`
4. `personal-db scheduler install && personal-db scheduler status`
5. Add MCP server to Claude Code; ask Claude to list trackers and log a habit. Verify it appears in `db.sqlite`.

**Open items deferred from the spec (do NOT block v0 sign-off):**
- Better entity-mirror direction semantics (spec §18)
- Cron schedules (only `every: <N><unit>` is wired in `_parse_every`; cron strings parse-but-noop in `_is_due`)
- gcal_events connector (v0.1)
- Auto-insight scheduling (v0.1)
- Cross-platform (v0.1)

---

*End of plan.*
