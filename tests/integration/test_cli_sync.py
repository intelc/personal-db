import subprocess
import sys

import yaml


def _init_with_tracker(tmp_path, ingest_body: str):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    d = root / "trackers" / "demo"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "description": "x",
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": "1h"},
                "time_column": "ts",
                "granularity": "event",
                "schema": {
                    "tables": {
                        "demo": {
                            "columns": {
                                "id": {"type": "TEXT", "semantic": "id"},
                                "ts": {"type": "TEXT", "semantic": "ts"},
                            }
                        }
                    }
                },
            }
        )
    )
    (d / "schema.sql").write_text("CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY, ts TEXT);")
    (d / "ingest.py").write_text(ingest_body)
    return root


def test_pdb_sync_runs_ingest(tmp_path):
    root = _init_with_tracker(
        tmp_path,
        "def backfill(t,start,end): pass\n"
        "def sync(t): t.upsert('demo', [{'id':'a','ts':'2026-04-25'}], key=['id'])\n",
    )
    r = subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "sync", "demo"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    import sqlite3

    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT id FROM demo").fetchone() == ("a",)


def test_pdb_log_inserts_row(tmp_path):
    root = _init_with_tracker(
        tmp_path,
        "def backfill(t,start,end): pass\ndef sync(t): pass\n",
    )
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "log",
            "demo",
            "id=manual1",
            "ts=2026-04-25",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
