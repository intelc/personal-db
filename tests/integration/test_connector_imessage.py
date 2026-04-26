import sqlite3
import subprocess
import sys

import yaml


def test_imessage_sync_resolves_people(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    # Pre-register Marko so the alias matches
    (root / "entities" / "people.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "display_name": "Marko Chen",
                    "aliases": ["marko@example.com", "+15551234567"],
                },
            ]
        )
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
            "imessage",
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("PERSONAL_DB_IMESSAGE_DB", "tests/fixtures/imessage/chat_mini.sqlite")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "sync",
            "imessage",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    con = sqlite3.connect(root / "db.sqlite")
    rows = con.execute(
        "SELECT person_id, COUNT(*) FROM imessage_messages "
        "WHERE person_id IS NOT NULL GROUP BY person_id"
    ).fetchall()
    # All 3 messages should resolve to the single Marko person_id
    assert len(rows) == 1 and rows[0][1] == 3
