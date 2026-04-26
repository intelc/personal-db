import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


def test_github_sync_inserts_rows_from_fixture(tmp_path, monkeypatch):
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
            "github_commits",
        ],
        check=True,
        capture_output=True,
    )
    fixture = json.loads(Path("tests/fixtures/github/commits_page1.json").read_text())
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("GITHUB_USER", "intel")

    # patch("requests.get") must run in the same process as sync_one.
    # subprocess.run would spawn a separate process where the patch has no effect,
    # so we call sync_one directly from this process.
    from personal_db.config import Config
    from personal_db.sync import sync_one

    cfg = Config(root=root)
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = fixture
        mock_get.return_value.headers = {"Link": ""}  # no next page
        sync_one(cfg, "github_commits")

    import sqlite3

    con = sqlite3.connect(root / "db.sqlite")
    n = con.execute("SELECT COUNT(*) FROM github_commits").fetchone()[0]
    # Note: each PushEvent in the fixture can produce multiple commit rows.
    # Compute the expected count from the fixture rather than `len(fixture)`.
    expected = sum(
        len(ev.get("payload", {}).get("commits", []))
        for ev in fixture
        if ev.get("type") == "PushEvent"
    )
    assert n == expected, f"expected {expected} commits, got {n}"
