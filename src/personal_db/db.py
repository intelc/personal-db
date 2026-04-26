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
