"""Exercises the finance app's declared mcp_tools entrypoints
(templates/apps/finance/tools.py) via the same loader the MCP server uses."""

from personal_db.core.apps import install_app_template
from personal_db.core.config import Config
from personal_db.core.entrypoints import load_entrypoint


def _load(dest, func_name):
    return load_entrypoint(dest, f"tools:{func_name}", modname_prefix="test_finance_tools")


def test_finance_enrich_receipt_stub_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")

    def fake_enrich(cfg, transaction_id, *, window_days=7, scope=None):
        assert transaction_id == "txn-1"
        assert window_days == 2
        assert scope == "Inbox"
        return {"run_id": "run-1", "status": "context_found", "result": {"receipt_message_ids": ["123"]}}

    monkeypatch.setattr(
        "personal_db.enrichments.finance.enrich_transaction_receipt_stub",
        fake_enrich,
    )

    func = _load(dest, "finance_enrich_receipt_stub")
    out = func(cfg, {"finance_transaction_id": "txn-1", "window_days": 2, "scope": "Inbox"})

    assert out["ok"] is True
    assert out["run_id"] == "run-1"


def test_finance_enrich_receipt_v1_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")

    def fake_enrich(cfg, transaction_id, *, window_days=7, scope=None, max_threads=3, max_candidate_threads=20):
        assert (transaction_id, window_days, scope, max_threads, max_candidate_threads) == (
            "txn-1", 2, "Inbox", 4, 9,
        )
        return {"run_id": "run-v1", "status": "enriched", "result": {"agent_result": {"merchant": "Store"}}}

    monkeypatch.setattr(
        "personal_db.enrichments.finance.enrich_transaction_receipt_v1",
        fake_enrich,
    )

    func = _load(dest, "finance_enrich_receipt_v1")
    out = func(
        cfg,
        {
            "finance_transaction_id": "txn-1",
            "window_days": 2,
            "scope": "Inbox",
            "max_threads": 4,
            "max_candidate_threads": 9,
        },
    )

    assert out["ok"] is True
    assert out["result"]["agent_result"]["merchant"] == "Store"


def test_finance_enqueue_receipt_jobs_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")

    def fake_enqueue(cfg, *, limit=50, window_days=7, scope=None, force=False):
        assert (limit, window_days, scope, force) == (3, 2, "Inbox", True)
        return {"enqueued": 1, "jobs": [{"job": {"input_id": "txn-1"}}]}

    monkeypatch.setattr(
        "personal_db.enrichments.finance.enqueue_missing_receipt_enrichments",
        fake_enqueue,
    )

    func = _load(dest, "finance_enqueue_receipt_jobs")
    out = func(cfg, {"limit": 3, "window_days": 2, "scope": "Inbox", "force": True})

    assert out["ok"] is True
    assert out["enqueued"] == 1


def test_finance_run_due_receipt_jobs_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")

    def fake_run_due(cfg, *, limit=5):
        assert limit == 4
        return {"ran": 1, "results": [{"ok": True}]}

    monkeypatch.setattr(
        "personal_db.enrichments.finance.run_due_finance_receipt_jobs",
        fake_run_due,
    )

    func = _load(dest, "finance_run_due_receipt_jobs")
    out = func(cfg, {"limit": 4})

    assert out["ok"] is True
    assert out["ran"] == 1


def test_finance_run_due_receipt_v1_jobs_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")

    def fake_run_due(cfg, *, limit=5):
        assert limit == 4
        return {"ran": 1, "results": [{"ok": True}]}

    monkeypatch.setattr(
        "personal_db.enrichments.finance.run_due_finance_receipt_v1_jobs",
        fake_run_due,
    )

    func = _load(dest, "finance_run_due_receipt_v1_jobs")
    out = func(cfg, {"limit": 4})

    assert out["ok"] is True
    assert out["ran"] == 1


def test_finance_receipt_latest_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")

    def fake_latest(cfg, enrichment_name, input_table, input_id):
        assert enrichment_name == "finance.transaction_receipt_v1"
        assert input_table == "finance_transactions"
        assert input_id == "txn-1"
        return {"status": "context_found"}

    monkeypatch.setattr(
        "personal_db.enrichments.core.get_latest_enrichment",
        fake_latest,
    )

    func = _load(dest, "finance_receipt_latest")
    out = func(cfg, {"finance_transaction_id": "txn-1", "v1": True})

    assert out["ok"] is True
    assert out["latest"]["status"] == "context_found"
