import contextlib
import sqlite3
from pathlib import Path

CORE_TABLES = (
    "people",
    "people_aliases",
    "topics",
    "topics_aliases",
    "notes",
    "tracker_schema_versions",
)

# Bumped when the shape of the core tables above changes. Stored in
# `PRAGMA user_version` (forward-only: init_db never lowers it), separate from
# any per-tracker `tracker_schema_versions` stamp, which tracks extension
# schemas instead.
CORE_SCHEMA_VERSION = 1

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
CREATE TABLE IF NOT EXISTS tracker_schema_versions (
  tracker    TEXT PRIMARY KEY,
  version    INTEGER NOT NULL,
  applied_at TEXT NOT NULL
);
"""


def _set_private_db_mode(db_path: Path) -> None:
    with contextlib.suppress(FileNotFoundError, PermissionError):
        db_path.chmod(0o600)


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with transaction(db_path) as con:
        con.executescript(_SCHEMA_SQL)
        # Whole-DB stamp for the core tables' shape. Forward-only: never lower
        # an existing db's user_version, even if CORE_SCHEMA_VERSION were ever
        # decreased by mistake.
        (current_version,) = con.execute("PRAGMA user_version").fetchone()
        if current_version < CORE_SCHEMA_VERSION:
            con.execute(f"PRAGMA user_version = {CORE_SCHEMA_VERSION}")


def connect(db_path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(db_path)
        _set_private_db_mode(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextlib.contextmanager
def connection(
    db_path: Path,
    *,
    read_only: bool = False,
    row_factory=None,
):
    con = connect(db_path, read_only=read_only)
    if row_factory is not None:
        con.row_factory = row_factory
    try:
        yield con
    finally:
        con.close()


@contextlib.contextmanager
def transaction(db_path: Path, *, row_factory=None):
    con = connect(db_path, read_only=False)
    if row_factory is not None:
        con.row_factory = row_factory
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def apply_tracker_schema(db_path: Path, schema_sql: str) -> None:
    """Run a tracker's schema.sql against the main db."""
    with transaction(db_path) as con:
        con.executescript(schema_sql)
