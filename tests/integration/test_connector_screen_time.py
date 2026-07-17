import sqlite3
import subprocess
import sys
from pathlib import Path

from personal_db.core.config import Config
from personal_db.core.sync import sync_one


def test_screen_time_sync_reads_fixture_db(tmp_path, monkeypatch):
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
            "screen_time",
        ],
        check=True,
        capture_output=True,
    )
    fixture = Path("tests/fixtures/screen_time/knowledgeC_mini.sqlite")
    monkeypatch.setenv("PERSONAL_DB_SCREEN_TIME_DB", str(fixture))
    # CLI sync now delegates to daemon (not running in tests); call sync_one directly.
    sync_one(Config(root=root), "screen_time")
    con = sqlite3.connect(root / "db.sqlite")
    n = con.execute("SELECT COUNT(*) FROM screen_time_app_usage").fetchone()[0]
    assert n == 3
