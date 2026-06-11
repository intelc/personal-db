import threading

import pytest

from personal_db.config import Config
from personal_db.worker import enrich as ew


def test_run_once_dispatches_finance_receipt_v1(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    calls = []

    def fake_run_due(c, *, limit, lease_seconds):
        calls.append((c, limit, lease_seconds))
        return {"ran": 1}

    monkeypatch.setattr(ew, "run_due_finance_receipt_v1_jobs", fake_run_due)

    result = ew.run_once(cfg, kind=ew.FINANCE_RECEIPT_V1, batch_size=3, lease_seconds=456)

    assert result == {"ran": 1}
    assert calls == [(cfg, 3, 456)]


def test_run_once_rejects_unknown_kind(tmp_root):
    cfg = Config(root=tmp_root)

    with pytest.raises(ValueError, match="unsupported"):
        ew.run_once(cfg, kind="unknown")


def test_run_loop_once_swallows_tick_errors(tmp_root, monkeypatch, caplog):
    cfg = Config(root=tmp_root)

    def failing_run_once(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ew, "run_once", failing_run_once)

    with caplog.at_level("ERROR", logger="personal_db.worker.enrich"):
        ew.run_loop(cfg, once=True)

    assert any("enrichment worker tick failed" in rec.message for rec in caplog.records)


def test_run_loop_stops_when_event_set(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    calls = []
    stop = threading.Event()

    def fake_run_once(*args, **kwargs):
        calls.append(1)
        stop.set()
        return {"ran": 0}

    monkeypatch.setattr(ew, "run_once", fake_run_once)

    ew.run_loop(cfg, interval_seconds=0.01, stop_event=stop)

    assert calls == [1]
