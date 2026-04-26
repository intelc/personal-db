"""Tests for the life_context tracker + log_life_context MCP tool."""

import sqlite3
import subprocess
import sys

import pytest

from personal_db.config import Config
from personal_db.mcp_server.tools import log_life_context


def _install(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root),
         "tracker", "install", "life_context"],
        check=True, capture_output=True,
    )
    return Config(root=root)


def test_log_single_day(tmp_path):
    cfg = _install(tmp_path)
    res = log_life_context(cfg, "2026-04-13", state="sick", note="flu")
    assert res["inserted"] == 1
    assert res["dates"] == ["2026-04-13"]

    con = sqlite3.connect(cfg.db_path)
    rows = con.execute(
        "SELECT date, state, note FROM life_context"
    ).fetchall()
    assert rows == [("2026-04-13", "sick", "flu")]


def test_log_range_fans_out_per_day(tmp_path):
    cfg = _install(tmp_path)
    res = log_life_context(cfg, "2026-04-13", "2026-04-18", state="sick", note="flu, low energy")
    assert res["inserted"] == 6
    assert res["dates"][0] == "2026-04-13"
    assert res["dates"][-1] == "2026-04-18"

    con = sqlite3.connect(cfg.db_path)
    rows = con.execute(
        "SELECT date, state, note FROM life_context ORDER BY date"
    ).fetchall()
    assert len(rows) == 6
    assert all(r[1] == "sick" and r[2] == "flu, low energy" for r in rows)


def test_log_note_only_no_state(tmp_path):
    """User types just free-text annotation, no categorical tag."""
    cfg = _install(tmp_path)
    res = log_life_context(cfg, "2026-04-12", note="computer broke, lost local data")
    assert res["inserted"] == 1
    con = sqlite3.connect(cfg.db_path)
    row = con.execute(
        "SELECT state, note FROM life_context WHERE date='2026-04-12'"
    ).fetchone()
    assert row[0] is None
    assert "computer broke" in row[1]


def test_log_requires_state_or_note(tmp_path):
    cfg = _install(tmp_path)
    with pytest.raises(ValueError, match="at least one of"):
        log_life_context(cfg, "2026-04-13")


def test_log_rejects_inverted_range(tmp_path):
    cfg = _install(tmp_path)
    with pytest.raises(ValueError, match="before start_date"):
        log_life_context(cfg, "2026-04-18", "2026-04-13", state="sick")


def test_log_rejects_bad_date_format(tmp_path):
    cfg = _install(tmp_path)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        log_life_context(cfg, "April 13", state="sick")


def test_multiple_entries_same_day_allowed(tmp_path):
    """Two entries on the same day shouldn't conflict — autoincrement id."""
    cfg = _install(tmp_path)
    log_life_context(cfg, "2026-04-13", state="sick")
    log_life_context(cfg, "2026-04-13", note="updated: actually it was a migraine")
    con = sqlite3.connect(cfg.db_path)
    n = con.execute("SELECT count(*) FROM life_context WHERE date='2026-04-13'").fetchone()[0]
    assert n == 2
