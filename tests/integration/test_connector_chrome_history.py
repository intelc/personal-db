"""Tests for chrome_history connector."""

import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from personal_db.config import Config
from personal_db.sync import sync_one


def _build_fake_chrome_history(path: Path):
    """Create a minimal fake Chrome History DB with 2 urls and 3 visits."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE urls (
          id INTEGER PRIMARY KEY,
          url LONGVARCHAR,
          title LONGVARCHAR,
          visit_count INTEGER
        );
        CREATE TABLE visits (
          id INTEGER PRIMARY KEY,
          url INTEGER,
          visit_time INTEGER,
          from_visit INTEGER,
          transition INTEGER,
          segment_id INTEGER,
          visit_duration INTEGER
        );
        """
    )
    # WebKit time for 2026-04-26T10:00:00Z = (date - 1601-01-01) in microseconds
    base_dt = datetime(2026, 4, 26, 10, 0, 0, tzinfo=UTC)
    epoch = datetime(1601, 1, 1, tzinfo=UTC)
    base_micros = int((base_dt - epoch).total_seconds() * 1_000_000)
    con.execute(
        "INSERT INTO urls(id, url, title, visit_count) VALUES (?, ?, ?, ?)",
        (1, "https://github.com/foo/bar", "foo/bar · GitHub", 3),
    )
    con.execute(
        "INSERT INTO urls(id, url, title, visit_count) VALUES (?, ?, ?, ?)",
        (2, "https://www.youtube.com/watch?v=abc", "Some Video - YouTube", 1),
    )
    con.execute(
        "INSERT INTO visits VALUES (1, 1, ?, 0, 0, 0, 30_000_000)",  # 30 sec dwell
        (base_micros,),
    )
    con.execute(
        "INSERT INTO visits VALUES (2, 1, ?, 0, 0, 0, 60_000_000)",  # 60 sec
        (base_micros + 1_800_000_000,),  # +30 min
    )
    con.execute(
        "INSERT INTO visits VALUES (3, 2, ?, 0, 0, 0, 600_000_000)",  # 10 min
        (base_micros + 3_600_000_000,),  # +1 hr
    )
    con.commit()
    con.close()


def _setup_chrome_dir(tmp_path: Path) -> Path:
    """Build a fake ~/Library/Application Support/Google/Chrome with one profile."""
    chrome_base = tmp_path / "Chrome"
    default = chrome_base / "Default"
    default.mkdir(parents=True)
    _build_fake_chrome_history(default / "History")
    return chrome_base


def test_chrome_history_inserts_visits(tmp_path, monkeypatch):
    chrome_base = _setup_chrome_dir(tmp_path)
    monkeypatch.setenv("CHROME_PROFILES_DIR", str(chrome_base))

    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "install",
            "chrome_history",
        ],
        check=True,
        capture_output=True,
    )
    cfg = Config(root=root)
    sync_one(cfg, "chrome_history")

    con = sqlite3.connect(cfg.db_path)
    rows = con.execute(
        "SELECT visit_id, profile, url, title, domain, visited_at, duration_seconds "
        "FROM chrome_visits ORDER BY visited_at"
    ).fetchall()
    assert len(rows) == 3
    # First visit
    vid, profile, url, _title, domain, visited_at, dur = rows[0]
    assert vid == 1
    assert profile == "Default"
    assert url == "https://github.com/foo/bar"
    assert domain == "github.com"
    assert visited_at.startswith("2026-04-26T10:00:00")
    assert abs(dur - 30.0) < 0.01
    # Third visit
    assert rows[2][4] == "www.youtube.com"
    assert abs(rows[2][6] - 600.0) < 0.01


def test_chrome_history_idempotent_with_cursor(tmp_path, monkeypatch):
    """Running sync twice should not re-insert; cursor advances past max visit_time."""
    chrome_base = _setup_chrome_dir(tmp_path)
    monkeypatch.setenv("CHROME_PROFILES_DIR", str(chrome_base))

    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "install",
            "chrome_history",
        ],
        check=True,
        capture_output=True,
    )
    cfg = Config(root=root)
    sync_one(cfg, "chrome_history")
    sync_one(cfg, "chrome_history")  # second run: should add 0

    con = sqlite3.connect(cfg.db_path)
    n = con.execute("SELECT COUNT(*) FROM chrome_visits").fetchone()[0]
    assert n == 3  # not 6


def test_chrome_history_handles_missing_chrome_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROME_PROFILES_DIR", str(tmp_path / "no-such-dir"))
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "install",
            "chrome_history",
        ],
        check=True,
        capture_output=True,
    )
    cfg = Config(root=root)
    sync_one(cfg, "chrome_history")  # should not raise

    con = sqlite3.connect(cfg.db_path)
    assert con.execute("SELECT COUNT(*) FROM chrome_visits").fetchone()[0] == 0
