from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INGEST_PY = REPO_ROOT / "src/personal_db/templates/trackers/code_agent_activity/ingest.py"


def _load_ingest_module():
    spec = importlib.util.spec_from_file_location("_pdb_code_agent_ingest", INGEST_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db_with_legacy_tables(tmp_path: Path) -> Path:
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE claude_sessions (
          session_id TEXT PRIMARY KEY,
          project_slug TEXT NOT NULL,
          started_at TEXT NOT NULL,
          last_msg_at TEXT NOT NULL,
          message_count INTEGER NOT NULL,
          user_msg_count INTEGER NOT NULL,
          assistant_msg_count INTEGER NOT NULL,
          first_user_prompt TEXT
        );
        CREATE TABLE codex_sessions (
          session_id TEXT PRIMARY KEY,
          cwd TEXT,
          started_at TEXT NOT NULL,
          last_event_at TEXT NOT NULL,
          event_count INTEGER NOT NULL,
          user_msg_count INTEGER NOT NULL,
          assistant_msg_count INTEGER NOT NULL,
          first_user_prompt TEXT
        );
        CREATE TABLE code_agent_sessions (
          agent TEXT NOT NULL,
          session_id TEXT NOT NULL,
          cwd TEXT,
          started_at TEXT NOT NULL,
          last_msg_at TEXT NOT NULL,
          message_count INTEGER NOT NULL,
          user_msg_count INTEGER NOT NULL,
          assistant_msg_count INTEGER NOT NULL,
          first_user_prompt TEXT,
          source_file TEXT,
          PRIMARY KEY (agent, session_id)
        );
        INSERT INTO claude_sessions VALUES ('c1','-x',  '2026-01-01T00:00:00Z','2026-01-01T01:00:00Z',5,3,2,'hi');
        INSERT INTO codex_sessions  VALUES ('x1','/repo','2026-01-02T00:00:00Z','2026-01-02T01:00:00Z',4,2,2,'hello');
    """)
    con.commit()
    con.close()
    return db


def test_table_exists(db_with_legacy_tables: Path):
    mod = _load_ingest_module()
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        assert mod._table_exists(con, "claude_sessions") is True
        assert mod._table_exists(con, "nonexistent") is False
    finally:
        con.close()


def test_run_legacy_migration_backfills_and_drops(db_with_legacy_tables: Path, tmp_path: Path):
    mod = _load_ingest_module()
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        mod._run_legacy_migration(con, tmp_path)
        assert mod._table_exists(con, "claude_sessions") is False
        assert mod._table_exists(con, "codex_sessions") is False
        rows = con.execute(
            "SELECT agent, session_id, cwd, first_user_prompt FROM code_agent_sessions ORDER BY agent"
        ).fetchall()
        assert rows == [
            ("claude_code", "c1", None, "hi"),
            ("codex_cli",   "x1", "/repo", "hello"),
        ]
    finally:
        con.close()


def test_run_legacy_migration_is_idempotent(db_with_legacy_tables: Path, tmp_path: Path):
    mod = _load_ingest_module()
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        mod._run_legacy_migration(con, tmp_path)
        before = con.execute("SELECT count(*) FROM code_agent_sessions").fetchone()[0]
        mod._run_legacy_migration(con, tmp_path)
        after = con.execute("SELECT count(*) FROM code_agent_sessions").fetchone()[0]
        assert before == after == 2
    finally:
        con.close()


def test_is_canonical_tracker_dir_accepts_canonical(tmp_path: Path):
    mod = _load_ingest_module()
    d = tmp_path / "claude_conversations"
    d.mkdir()
    for name in ("manifest.yaml", "ingest.py", "schema.sql", "visualizations.py"):
        (d / name).write_text("# test")
    (d / "__pycache__").mkdir()
    assert mod._is_canonical_tracker_dir(d) is True


def test_is_canonical_tracker_dir_rejects_extra_file(tmp_path: Path):
    mod = _load_ingest_module()
    d = tmp_path / "claude_conversations"
    d.mkdir()
    for name in ("manifest.yaml", "ingest.py", "schema.sql", "visualizations.py", "user_custom.py"):
        (d / name).write_text("# test")
    assert mod._is_canonical_tracker_dir(d) is False


def test_run_legacy_migration_removes_canonical_tracker_dirs(db_with_legacy_tables: Path, tmp_path: Path):
    mod = _load_ingest_module()
    trackers = tmp_path / "trackers"
    trackers.mkdir()
    for name in ("claude_conversations", "codex_conversations"):
        d = trackers / name
        d.mkdir()
        for fn in ("manifest.yaml", "ingest.py", "schema.sql", "visualizations.py"):
            (d / fn).write_text("# test")
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        mod._run_legacy_migration(con, tmp_path)
    finally:
        con.close()
    assert not (trackers / "claude_conversations").exists()
    assert not (trackers / "codex_conversations").exists()


def test_run_legacy_migration_preserves_non_canonical_dir(db_with_legacy_tables: Path, tmp_path: Path):
    mod = _load_ingest_module()
    trackers = tmp_path / "trackers"
    trackers.mkdir()
    d = trackers / "claude_conversations"
    d.mkdir()
    for fn in ("manifest.yaml", "ingest.py", "schema.sql", "visualizations.py", "user_custom.py"):
        (d / fn).write_text("# test")
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        mod._run_legacy_migration(con, tmp_path)
    finally:
        con.close()
    assert (trackers / "claude_conversations").exists()
    assert (trackers / "claude_conversations" / "user_custom.py").exists()
