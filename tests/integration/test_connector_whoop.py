import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from personal_db.config import Config
from personal_db.oauth import save_token
from personal_db.sync import sync_one


def test_whoop_sync_inserts_cycles(tmp_path, monkeypatch):
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
            "whoop",
        ],
        check=True,
        capture_output=True,
    )
    cfg = Config(root=root)
    save_token(
        cfg,
        "whoop",
        {
            "access_token": "x",
            "refresh_token": "r",
            "expires_at": 9999999999,
        },
    )
    monkeypatch.setenv("WHOOP_CLIENT_ID", "fake_id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "fake_secret")
    fixture = json.loads(Path("tests/fixtures/whoop/cycles.json").read_text())
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"records": fixture, "next_token": None}
        sync_one(cfg, "whoop")

    import sqlite3

    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT COUNT(*) FROM whoop_cycles").fetchone()[0] == len(fixture)
