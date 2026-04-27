import sqlite3
import subprocess
import sys


def test_setup_with_name_arg_runs_runner_for_that_tracker(tmp_path):
    """`personal-db tracker setup habits` runs habits' setup + test sync.

    habits' manifest has one instructions step that prompts "Press Enter when done".
    We send "\\n" via stdin to satisfy that prompt in the no-TTY subprocess.
    """
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
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "setup",
            "habits",
        ],
        capture_output=True,
        text=True,
        input="\n",
    )
    assert r.returncode == 0, r.stderr
    con = sqlite3.connect(root / "db.sqlite")
    con.execute("SELECT * FROM habits")  # raises if table missing


def test_setup_help_lists_subcommand(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "tracker", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "setup" in r.stdout
