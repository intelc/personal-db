import json
from pathlib import Path

import yaml

from personal_db.config import Config
from personal_db.db import init_db
from personal_db.sync import _is_due, sync_one


def _make_tracker_dir(tmp_root: Path, name: str, schedule_every: str = "1h"):
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": schedule_every},
                "time_column": "ts",
                "granularity": "event",
                "schema": {
                    "tables": {
                        name: {
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
    (d / "schema.sql").write_text(
        f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, ts TEXT);"
    )
    (d / "ingest.py").write_text(
        "def backfill(t, start, end):\n"
        "    t.upsert(t.name, [{'id': 'b1', 'ts': '2026-04-01'}], key=['id'])\n"
        "def sync(t):\n"
        "    t.upsert(t.name, [{'id': 's1', 'ts': '2026-04-25'}], key=['id'])\n"
    )
    return d


def test_sync_one_runs_ingest_and_records_last_run(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(tmp_root, "demo")
    sync_one(cfg, "demo")
    last_run = json.loads((tmp_root / "state" / "last_run.json").read_text())
    assert "demo" in last_run


def test_is_due_when_never_run(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(tmp_root, "demo", schedule_every="1h")
    assert _is_due(cfg, "demo") is True


def test_sync_due_skips_recent(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(tmp_root, "demo", schedule_every="1h")
    sync_one(cfg, "demo")
    # Immediately due-check should be false
    assert _is_due(cfg, "demo") is False
