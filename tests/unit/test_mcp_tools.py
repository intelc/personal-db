import pytest
import yaml
from unittest.mock import patch

from personal_db.config import Config
from personal_db.daemon import client as dc
from personal_db.db import apply_tracker_schema, connect, init_db
from personal_db.mcp_server.tools import (
    backfill_tool,
    describe_tracker,
    get_series,
    list_trackers,
    log_event_tool,
    query,
    sync_due_tool,
    sync_tool,
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


def _make_runnable_tracker(tmp_root, name="runnable"):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "runnable",
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": "1h"},
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
    return cfg


def test_sync_tool_delegates_to_daemon(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "sync_one", return_value={"ok": True, "tracker": "runnable"}) as m:
        out = sync_tool(cfg, "runnable")
    m.assert_called_once_with("runnable")
    assert out == {"ok": True, "tracker": "runnable"}


def test_sync_tool_returns_structured_error_on_unreachable(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "sync_one", side_effect=dc.DaemonUnreachable("nope")):
        out = sync_tool(cfg, "runnable")
    assert out["ok"] is False
    assert "daemon" in out["error"].lower()


def test_sync_due_tool_delegates(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "sync_due", return_value={"results": {"runnable": "ok"}}) as m:
        out = sync_due_tool(cfg)
    m.assert_called_once_with()
    assert out["results"]["runnable"] == "ok"


def test_sync_due_tool_unreachable(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "sync_due", side_effect=dc.DaemonUnreachable("nope")):
        out = sync_due_tool(cfg)
    assert out["ok"] is False
    assert "daemon" in out["error"].lower()


def test_backfill_tool_delegates(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "backfill", return_value={"ok": True}) as m:
        out = backfill_tool(cfg, "runnable", "2026-04-01", "2026-04-02")
    m.assert_called_once_with("runnable", "2026-04-01", "2026-04-02")
    assert out["ok"] is True


def test_backfill_tool_unreachable(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "backfill", side_effect=dc.DaemonUnreachable("nope")):
        out = backfill_tool(cfg, "runnable")
    assert out["ok"] is False
