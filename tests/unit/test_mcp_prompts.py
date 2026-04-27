"""Unit tests for the create_tracker MCP prompt builder."""

import sqlite3

import yaml

from personal_db.config import Config
from personal_db.mcp_server.prompts import build_create_tracker_prompt


def _init_root(tmp_path):
    root = tmp_path / "personal_db"
    (root / "trackers").mkdir(parents=True)
    return root


def test_create_tracker_prompt_substitutes_paths(tmp_path):
    root = _init_root(tmp_path)
    cfg = Config(root=root)
    text = build_create_tracker_prompt(cfg)
    # No placeholders left
    assert "{{root_path}}" not in text
    assert "{{tables_summary}}" not in text
    assert "{{installed_trackers}}" not in text
    assert "{{trackers_dir}}" not in text
    assert "{{db_path}}" not in text
    # Real values present
    assert str(root) in text
    assert str(cfg.trackers_dir) in text
    assert str(cfg.db_path) in text


def test_create_tracker_prompt_lists_user_tables(tmp_path):
    root = _init_root(tmp_path)
    cfg = Config(root=root)
    # Seed a couple of source tables
    con = sqlite3.connect(cfg.db_path)
    con.executescript(
        """
        CREATE TABLE foo (id INTEGER PRIMARY KEY, label TEXT);
        INSERT INTO foo VALUES (1, 'a'), (2, 'b');
        CREATE TABLE bar (ts TEXT, value REAL);
        """
    )
    con.commit()
    con.close()
    text = build_create_tracker_prompt(cfg)
    assert "`foo`" in text
    assert "`bar`" in text
    assert "2 rows" in text  # foo
    assert "label TEXT" in text
    # sqlite internals filtered out
    assert "sqlite_sequence" not in text


def test_create_tracker_prompt_lists_installed_trackers(tmp_path):
    root = _init_root(tmp_path)
    # Fake an installed tracker
    d = root / "trackers" / "demo_tracker"
    d.mkdir()
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo_tracker",
                "description": "a demo description",
                "granularity": "day",
            }
        )
    )
    cfg = Config(root=root)
    text = build_create_tracker_prompt(cfg)
    assert "demo_tracker" in text
    assert "a demo description" in text


def test_create_tracker_prompt_handles_empty_state(tmp_path):
    """Brand-new install: no DB, no trackers — prompt should still render usefully."""
    root = _init_root(tmp_path)
    cfg = Config(root=root)
    text = build_create_tracker_prompt(cfg)
    # Should call out the empty DB explicitly so Claude doesn't hallucinate tables
    assert "none" in text.lower()  # in the tables section
