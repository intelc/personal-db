import pytest
import yaml

from personal_db.config import Config
from personal_db.db import apply_tracker_schema, connect, init_db
from personal_db.mcp_server.tools import (
    describe_tracker,
    get_series,
    list_trackers,
    log_event_tool,
    query,
)


def _make_demo(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / "demo"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "description": "demo tracker",
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
                                "ts": {"type": "TEXT", "semantic": "iso ts"},
                                "value": {"type": "INTEGER", "semantic": "v"},
                            }
                        }
                    }
                },
            }
        )
    )
    apply_tracker_schema(
        cfg.db_path,
        "CREATE TABLE demo (id TEXT PRIMARY KEY, ts TEXT, value INTEGER);",
    )
    con = connect(cfg.db_path)
    con.executemany(
        "INSERT INTO demo VALUES (?,?,?)",
        [
            ("a", "2026-04-25T08:00", 1),
            ("b", "2026-04-25T09:00", 2),
            ("c", "2026-04-26T08:00", 3),
        ],
    )
    con.commit()
    con.close()
    return cfg


def test_list_trackers(tmp_root):
    cfg = _make_demo(tmp_root)
    out = list_trackers(cfg)
    assert any(t["name"] == "demo" for t in out)


def test_describe_tracker(tmp_root):
    cfg = _make_demo(tmp_root)
    out = describe_tracker(cfg, "demo")
    assert out["time_column"] == "ts"
    assert "demo" in out["schema"]["tables"]


def test_query_select_only(tmp_root):
    cfg = _make_demo(tmp_root)
    rows = query(cfg, "SELECT id FROM demo ORDER BY id")
    assert [r["id"] for r in rows] == ["a", "b", "c"]


def test_query_rejects_writes(tmp_root):
    cfg = _make_demo(tmp_root)
    with pytest.raises(ValueError):
        query(cfg, "INSERT INTO demo VALUES ('x', 'now', 9)")
    with pytest.raises(ValueError):
        query(cfg, "SELECT 1; DROP TABLE demo")


def test_get_series_groups_by_day(tmp_root):
    cfg = _make_demo(tmp_root)
    # Range ends at 2026-04-27 (exclusive) so both April 25 and 26 rows are included.
    rows = get_series(
        cfg,
        tracker="demo",
        range_="2026-04-25/2026-04-27",
        granularity="day",
        agg="sum",
        value_column="value",
    )
    by_day = {r["bucket"]: r["value"] for r in rows}
    assert by_day["2026-04-25"] == 3
    assert by_day["2026-04-26"] == 3


def test_log_event_tool(tmp_root):
    cfg = _make_demo(tmp_root)
    rid = log_event_tool(cfg, "demo", {"id": "z", "ts": "2026-04-27", "value": 5})
    assert rid is not None
