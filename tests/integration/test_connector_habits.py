import subprocess
import sys


def test_habits_log_via_cli(tmp_path):
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
            "habits",
        ],
        check=True,
        capture_output=True,
    )
    # Schema must be applied — sync once with a no-op ingest does that
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "sync", "habits"],
        check=True,
        capture_output=True,
    )
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "log",
            "habits",
            "name=meditate",
            "value=1",
            "ts=2026-04-25T08:00",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    import sqlite3

    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT name,value FROM habits").fetchone() == ("meditate", "1")
