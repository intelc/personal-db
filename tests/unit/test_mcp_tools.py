from unittest.mock import patch

import pytest
import yaml

from personal_db.core.config import Config
from personal_db.services.daemon import client as dc
from personal_db.core.db import apply_tracker_schema, connect, init_db
from personal_db.services.mcp_server.tools import (
    backfill_tool,
    describe_tracker,
    email_search_receipts,
    enrichment_job_cancel,
    enrichment_job_retry,
    enrichment_job_show,
    enrichment_jobs_list,
    enrichment_queue_summary,
    get_series,
    list_remote_sources,
    list_trackers,
    log_event_tool,
    query,
    sync_due_tool,
    sync_tool,
)
from personal_db.core.sources import install_source_template


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


def test_list_remote_sources_returns_installed_sources(tmp_root):
    cfg = Config(root=tmp_root)
    install_source_template(cfg, "spark_email")

    out = list_remote_sources(cfg)

    assert out[0]["name"] == "spark_email"
    assert out[0]["provider"] == "spark"
    assert "search" in out[0]["capabilities"]


def test_email_search_receipts_returns_context_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    class FakeProvider:
        @classmethod
        def from_config(cls, cfg):
            return cls()

        def search_receipts(
            self,
            *,
            merchant=None,
            amount=None,
            date_=None,
            window_days=7,
            scope=None,
        ):
            assert merchant == "Store"
            assert amount == "12.34"
            assert date_ == "2026-06-01"
            assert window_days == 3
            assert scope == "Inbox"

            class Result:
                def as_dict(self):
                    return {
                        "provider": "email",
                        "operation": "search_receipts",
                        "query": {"merchant": "Store"},
                        "evidence": [{"ref": "spark_email:message:123"}],
                        "data": {"email_ids": ["123"]},
                        "raw_text": "raw",
                    }

            return Result()

    monkeypatch.setattr(
        "personal_db.services.mcp_server.tools.SparkEmailContextProvider",
        FakeProvider,
    )

    out = email_search_receipts(cfg, "Store", "12.34", "2026-06-01", 3, "Inbox")

    assert out["ok"] is True
    assert out["provider"] == "email"
    assert out["evidence"][0]["ref"] == "spark_email:message:123"


def test_enrichment_queue_control_tools_wrap_results(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    monkeypatch.setattr(
        "personal_db.services.mcp_server.tools.list_enrichment_jobs",
        lambda cfg, **kwargs: [{"job_id": "job-1", **kwargs}],
    )
    monkeypatch.setattr(
        "personal_db.services.mcp_server.tools.get_enrichment_job_detail",
        lambda cfg, job_id: {"job": {"job_id": job_id}, "last_run": None, "latest": None},
    )
    monkeypatch.setattr(
        "personal_db.services.mcp_server.tools.retry_enrichment_job",
        lambda cfg, job_id, *, reset_attempts=True: {
            "job_id": job_id,
            "status": "pending",
            "reset_attempts": reset_attempts,
        },
    )
    monkeypatch.setattr(
        "personal_db.services.mcp_server.tools.cancel_enrichment_job",
        lambda cfg, job_id, *, reason=None: {
            "job_id": job_id,
            "status": "canceled",
            "last_error": f"canceled: {reason}",
        },
    )

    listed = enrichment_jobs_list(cfg, status="failed", limit=3)
    shown = enrichment_job_show(cfg, "job-1")
    retried = enrichment_job_retry(cfg, "job-1", reset_attempts=False)
    canceled = enrichment_job_cancel(cfg, "job-1", reason="manual")

    assert listed["ok"] is True
    assert listed["jobs"][0]["status"] == "failed"
    assert listed["jobs"][0]["limit"] == 3
    assert shown["job"]["job_id"] == "job-1"
    assert retried["job"]["reset_attempts"] is False
    assert canceled["job"]["status"] == "canceled"


def test_enrichment_queue_summary_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    monkeypatch.setattr(
        "personal_db.services.mcp_server.tools.get_enrichment_queue_summary",
        lambda cfg: {"by_enrichment": {"x": {"statuses": {"pending": 1}}}, "failed_jobs": []},
    )

    out = enrichment_queue_summary(cfg)

    assert out["ok"] is True
    assert out["by_enrichment"]["x"]["statuses"]["pending"] == 1


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
