"""Tests for the data_horizon module: per-tracker earliest-date metadata."""

import sqlite3

import yaml

from personal_db.config import Config
from personal_db.data_horizon import compute_and_store, get, get_all
from personal_db.manifest import Manifest


def _make_manifest(name: str, time_col: str, table: str, *, local_only: bool) -> Manifest:
    return Manifest.model_validate(
        {
            "name": name,
            "description": "x",
            "permission_type": "none",
            "setup_steps": [],
            "schedule": {"every": "1h"},
            "time_column": time_col,
            "granularity": "event",
            "schema": {"tables": {table: {"columns": {time_col: {"type": "TEXT", "semantic": "ts"}}}}},
            "local_only": local_only,
        }
    )


def _seed_table(db_path, table, time_col, values):
    con = sqlite3.connect(db_path)
    con.execute(f'CREATE TABLE {table} ({time_col} TEXT)')
    con.executemany(f'INSERT INTO {table}({time_col}) VALUES (?)', [(v,) for v in values])
    con.commit()
    con.close()


def test_compute_horizon_for_local_only_tracker(tmp_path):
    cfg = Config(root=tmp_path / "personal_db")
    cfg.trackers_dir.mkdir(parents=True)
    _seed_table(cfg.db_path, "screen_time_app_usage", "start_at",
                ["2026-04-13T10:00:00+00:00", "2026-04-15T10:00:00+00:00"])
    m = _make_manifest("screen_time", "start_at", "screen_time_app_usage", local_only=True)
    h = compute_and_store(cfg, "screen_time", m)
    assert h == "2026-04-13T10:00:00+00:00"
    assert get(cfg, "screen_time") == "2026-04-13T10:00:00+00:00"


def test_horizon_skipped_for_non_local_tracker(tmp_path):
    cfg = Config(root=tmp_path / "personal_db")
    cfg.trackers_dir.mkdir(parents=True)
    _seed_table(cfg.db_path, "github_commits", "committed_at", ["2025-01-01T00:00:00Z"])
    m = _make_manifest("github_commits", "committed_at", "github_commits", local_only=False)
    assert compute_and_store(cfg, "github_commits", m) is None
    assert get(cfg, "github_commits") is None


def test_horizon_handles_missing_table(tmp_path):
    """Local-only tracker with no synced rows yet — should not crash, returns None."""
    cfg = Config(root=tmp_path / "personal_db")
    cfg.trackers_dir.mkdir(parents=True)
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(cfg.db_path).close()  # empty db
    m = _make_manifest("imessage", "sent_at", "imessage_messages", local_only=True)
    assert compute_and_store(cfg, "imessage", m) is None


def test_horizon_overwrites_on_resync(tmp_path):
    """If older data appears later (e.g. backfill), the stored horizon updates."""
    cfg = Config(root=tmp_path / "personal_db")
    cfg.trackers_dir.mkdir(parents=True)
    _seed_table(cfg.db_path, "imessage_messages", "sent_at", ["2026-04-13T00:00:00+00:00"])
    m = _make_manifest("imessage", "sent_at", "imessage_messages", local_only=True)
    assert compute_and_store(cfg, "imessage", m) == "2026-04-13T00:00:00+00:00"
    # Now older data shows up
    con = sqlite3.connect(cfg.db_path)
    con.execute("INSERT INTO imessage_messages(sent_at) VALUES (?)", ("2026-03-01T00:00:00+00:00",))
    con.commit()
    con.close()
    assert compute_and_store(cfg, "imessage", m) == "2026-03-01T00:00:00+00:00"


def test_get_all_returns_every_recorded_horizon(tmp_path):
    cfg = Config(root=tmp_path / "personal_db")
    cfg.trackers_dir.mkdir(parents=True)
    _seed_table(cfg.db_path, "a", "ts", ["2026-04-13T00:00:00Z"])
    _seed_table(cfg.db_path, "b", "ts", ["2026-04-15T00:00:00Z"])
    m1 = _make_manifest("a", "ts", "a", local_only=True)
    m2 = _make_manifest("b", "ts", "b", local_only=True)
    compute_and_store(cfg, "a", m1)
    compute_and_store(cfg, "b", m2)
    all_h = get_all(cfg)
    assert all_h == {"a": "2026-04-13T00:00:00Z", "b": "2026-04-15T00:00:00Z"}


def test_get_returns_none_when_table_missing(tmp_path):
    cfg = Config(root=tmp_path / "personal_db")
    cfg.trackers_dir.mkdir(parents=True)
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(cfg.db_path).close()
    assert get(cfg, "anything") is None
    assert get_all(cfg) == {}


def test_manifest_local_only_defaults_false():
    """Existing manifests without the field should still validate."""
    m = Manifest.model_validate(yaml.safe_load("""
name: x
description: x
permission_type: none
setup_steps: []
schedule: {every: 1h}
time_column: ts
granularity: event
schema: {tables: {x: {columns: {ts: {type: TEXT, semantic: ts}}}}}
"""))
    assert m.local_only is False
