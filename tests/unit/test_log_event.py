import pytest
import yaml

from personal_db.config import Config
from personal_db.db import apply_tracker_schema, connect, init_db
from personal_db.log_event import log_event


def _setup_habits(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / "habits"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "habits",
                "description": "habit log",
                "permission_type": "manual",
                "setup_steps": [],
                "time_column": "ts",
                "granularity": "event",
                "schema": {
                    "tables": {
                        "habits": {
                            "columns": {
                                "id": {"type": "INTEGER", "semantic": "rowid"},
                                "name": {"type": "TEXT", "semantic": "habit name"},
                                "value": {"type": "INTEGER", "semantic": "1=did, 0=didnt"},
                                "ts": {"type": "TEXT", "semantic": "event time"},
                            }
                        }
                    }
                },
            }
        )
    )
    apply_tracker_schema(
        cfg.db_path,
        "CREATE TABLE habits (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, value INTEGER, ts TEXT);",
    )
    return cfg


def test_log_event_inserts_row(tmp_root):
    cfg = _setup_habits(tmp_root)
    log_event(cfg, "habits", {"name": "meditate", "value": 1, "ts": "2026-04-25T08:00"})
    con = connect(cfg.db_path)
    row = con.execute("SELECT name,value FROM habits").fetchone()
    assert row == ("meditate", 1)


def test_log_event_rejects_unknown_field(tmp_root):
    cfg = _setup_habits(tmp_root)
    with pytest.raises(ValueError):
        log_event(cfg, "habits", {"name": "x", "value": 1, "ts": "now", "ghost": 7})
