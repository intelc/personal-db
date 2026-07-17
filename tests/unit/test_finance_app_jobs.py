"""Exercises the finance app's declared background_jobs entrypoints
(templates/apps/finance/jobs.py) the way the daemon actually invokes them:
installed into a root, then loaded via core.entrypoints.load_entrypoint."""

from personal_db.core.apps import install_app_template
from personal_db.core.config import Config
from personal_db.core.entrypoints import load_entrypoint


def test_enqueue_receipt_v1_jobs_delegates_with_expected_defaults(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")
    calls = []

    def fake_enqueue(cfg, *, limit, window_days, max_threads, max_candidate_threads):
        calls.append((limit, window_days, max_threads, max_candidate_threads))
        return {"enqueued": 3}

    monkeypatch.setattr(
        "personal_db.enrichments.finance.enqueue_missing_receipt_v1_enrichments",
        fake_enqueue,
    )

    func = load_entrypoint(dest, "jobs:enqueue_receipt_v1_jobs", modname_prefix="test_finance_jobs")
    result = func(cfg)

    assert result == {"enqueued": 3}
    assert calls == [(20, 7, 3, 20)]


def test_run_due_receipt_v1_jobs_delegates_with_expected_defaults(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")
    calls = []

    def fake_run_due(cfg, *, limit, lease_seconds):
        calls.append((limit, lease_seconds))
        return {"ran": 2}

    monkeypatch.setattr(
        "personal_db.enrichments.finance.run_due_finance_receipt_v1_jobs",
        fake_run_due,
    )

    func = load_entrypoint(dest, "jobs:run_due_receipt_v1_jobs", modname_prefix="test_finance_jobs")
    result = func(cfg)

    assert result == {"ran": 2}
    assert calls == [(2, 600)]
