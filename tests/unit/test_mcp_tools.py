from unittest.mock import patch

import pytest
import yaml

from personal_db.config import Config
from personal_db.daemon import client as dc
from personal_db.db import apply_tracker_schema, connect, init_db
from personal_db.mcp_server.tools import (
    backfill_tool,
    describe_tracker,
    email_search_receipts,
    enrichment_job_cancel,
    enrichment_job_retry,
    enrichment_job_show,
    enrichment_jobs_list,
    enrichment_queue_summary,
    finance_enqueue_receipt_jobs,
    finance_enqueue_receipt_v1_jobs,
    finance_enrich_receipt_stub,
    finance_enrich_receipt_v1,
    finance_receipt_latest,
    finance_run_due_receipt_jobs,
    finance_run_due_receipt_v1_jobs,
    get_series,
    list_remote_sources,
    list_trackers,
    log_event_tool,
    query,
    spark_email_folders,
    sync_due_tool,
    sync_tool,
    worker_log_tail,
    worker_status,
)
from personal_db.sources import install_source_template


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


def test_spark_email_folders_returns_structured_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    class FakeSpark:
        @classmethod
        def from_config(cls, cfg, require_installed=False):
            assert require_installed is True
            return cls()

        def folders(self, scope=None):
            assert scope == "Inbox"

            class Result:
                def as_dict(self):
                    return {
                        "source": "spark_email",
                        "operation": "folders",
                        "data": {"groups": []},
                        "raw_text": "Unified\n",
                    }

            return Result()

    monkeypatch.setattr("personal_db.mcp_server.tools.SparkEmailSource", FakeSpark)

    out = spark_email_folders(cfg, "Inbox")

    assert out["ok"] is True
    assert out["source"] == "spark_email"
    assert out["operation"] == "folders"


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
        "personal_db.mcp_server.tools.SparkEmailContextProvider",
        FakeProvider,
    )

    out = email_search_receipts(cfg, "Store", "12.34", "2026-06-01", 3, "Inbox")

    assert out["ok"] is True
    assert out["provider"] == "email"
    assert out["evidence"][0]["ref"] == "spark_email:message:123"


def test_finance_enrich_receipt_stub_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    def fake_enrich(cfg, transaction_id, *, window_days=7, scope=None):
        assert transaction_id == "txn-1"
        assert window_days == 2
        assert scope == "Inbox"
        return {
            "run_id": "run-1",
            "status": "context_found",
            "result": {"receipt_message_ids": ["123"]},
        }

    monkeypatch.setattr(
        "personal_db.mcp_server.tools.enrich_transaction_receipt_stub",
        fake_enrich,
    )

    out = finance_enrich_receipt_stub(cfg, "txn-1", 2, "Inbox")

    assert out["ok"] is True
    assert out["run_id"] == "run-1"
    assert out["result"]["receipt_message_ids"] == ["123"]


def test_finance_enrich_receipt_v1_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    def fake_enrich(
        cfg,
        transaction_id,
        *,
        window_days=7,
        scope=None,
        max_threads=3,
        max_candidate_threads=20,
    ):
        assert transaction_id == "txn-1"
        assert window_days == 2
        assert scope == "Inbox"
        assert max_threads == 4
        assert max_candidate_threads == 9
        return {
            "run_id": "run-v1",
            "status": "enriched",
            "result": {"agent_result": {"merchant": "Store"}},
        }

    monkeypatch.setattr(
        "personal_db.mcp_server.tools.enrich_transaction_receipt_v1",
        fake_enrich,
    )

    out = finance_enrich_receipt_v1(cfg, "txn-1", 2, "Inbox", 4, 9)

    assert out["ok"] is True
    assert out["run_id"] == "run-v1"
    assert out["result"]["agent_result"]["merchant"] == "Store"


def test_finance_enqueue_receipt_jobs_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    def fake_enqueue(cfg, *, limit=50, window_days=7, scope=None, force=False):
        assert limit == 3
        assert window_days == 2
        assert scope == "Inbox"
        assert force is True
        return {"enqueued": 1, "jobs": [{"job": {"input_id": "txn-1"}}]}

    monkeypatch.setattr(
        "personal_db.mcp_server.tools.enqueue_missing_receipt_enrichments",
        fake_enqueue,
    )

    out = finance_enqueue_receipt_jobs(cfg, 3, 2, "Inbox", True)

    assert out["ok"] is True
    assert out["enqueued"] == 1


def test_finance_enqueue_receipt_v1_jobs_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    def fake_enqueue(
        cfg,
        *,
        limit=50,
        window_days=7,
        scope=None,
        max_threads=3,
        max_candidate_threads=20,
        force=False,
        start_date=None,
        end_date=None,
        snippet_window_chars=300,
        only_ready=False,
    ):
        assert limit == 3
        assert window_days == 2
        assert scope == "Inbox"
        assert max_threads == 4
        assert max_candidate_threads == 9
        assert force is True
        assert start_date == "2026-06-01"
        assert end_date == "2026-06-02"
        assert snippet_window_chars == 180
        assert only_ready is True
        return {"enqueued": 1, "jobs": [{"job": {"input_id": "txn-1"}}]}

    monkeypatch.setattr(
        "personal_db.mcp_server.tools.enqueue_missing_receipt_v1_enrichments",
        fake_enqueue,
    )

    out = finance_enqueue_receipt_v1_jobs(
        cfg,
        3,
        2,
        "Inbox",
        4,
        9,
        True,
        "2026-06-01",
        "2026-06-02",
        180,
        True,
    )

    assert out["ok"] is True
    assert out["enqueued"] == 1


def test_finance_run_due_receipt_jobs_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    def fake_run_due(cfg, *, limit=5):
        assert limit == 4
        return {"ran": 1, "results": [{"ok": True}]}

    monkeypatch.setattr(
        "personal_db.mcp_server.tools.run_due_finance_receipt_jobs",
        fake_run_due,
    )

    out = finance_run_due_receipt_jobs(cfg, 4)

    assert out["ok"] is True
    assert out["ran"] == 1


def test_finance_run_due_receipt_v1_jobs_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    def fake_run_due(cfg, *, limit=5):
        assert limit == 4
        return {"ran": 1, "results": [{"ok": True}]}

    monkeypatch.setattr(
        "personal_db.mcp_server.tools.run_due_finance_receipt_v1_jobs",
        fake_run_due,
    )

    out = finance_run_due_receipt_v1_jobs(cfg, 4)

    assert out["ok"] is True
    assert out["ran"] == 1


def test_enrichment_queue_control_tools_wrap_results(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    monkeypatch.setattr(
        "personal_db.mcp_server.tools.list_enrichment_jobs",
        lambda cfg, **kwargs: [{"job_id": "job-1", **kwargs}],
    )
    monkeypatch.setattr(
        "personal_db.mcp_server.tools.get_enrichment_job_detail",
        lambda cfg, job_id: {"job": {"job_id": job_id}, "last_run": None, "latest": None},
    )
    monkeypatch.setattr(
        "personal_db.mcp_server.tools.retry_enrichment_job",
        lambda cfg, job_id, *, reset_attempts=True: {
            "job_id": job_id,
            "status": "pending",
            "reset_attempts": reset_attempts,
        },
    )
    monkeypatch.setattr(
        "personal_db.mcp_server.tools.cancel_enrichment_job",
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


def test_finance_receipt_latest_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    def fake_latest(cfg, enrichment_name, input_table, input_id):
        assert enrichment_name == "finance.transaction_receipt_v1"
        assert input_table == "finance_transactions"
        assert input_id == "txn-1"
        return {"status": "context_found"}

    monkeypatch.setattr("personal_db.mcp_server.tools.get_latest_enrichment", fake_latest)

    out = finance_receipt_latest(cfg, "txn-1", v1=True)

    assert out["ok"] is True
    assert out["latest"]["status"] == "context_found"


def test_enrichment_queue_summary_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    monkeypatch.setattr(
        "personal_db.mcp_server.tools.get_enrichment_queue_summary",
        lambda cfg: {"by_enrichment": {"x": {"statuses": {"pending": 1}}}, "failed_jobs": []},
    )

    out = enrichment_queue_summary(cfg)

    assert out["ok"] is True
    assert out["by_enrichment"]["x"]["statuses"]["pending"] == 1


def test_worker_status_and_log_tail_wrap_results(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)

    monkeypatch.setattr(
        "personal_db.mcp_server.tools.worker_install.info",
        lambda root: {"installed": True, "loaded": True, "label": "worker"},
    )
    monkeypatch.setattr(
        "personal_db.mcp_server.tools.worker_install.log_tail",
        lambda root, *, lines=50: {"exists": True, "lines": ["hello"], "lines_arg": lines},
    )

    status = worker_status(cfg)
    log = worker_log_tail(cfg, lines=3)

    assert status["ok"] is True
    assert status["loaded"] is True
    assert log["ok"] is True
    assert log["lines"] == ["hello"]
    assert log["lines_arg"] == 3


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
