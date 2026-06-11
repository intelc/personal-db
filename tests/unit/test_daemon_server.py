import threading
import time
from unittest.mock import patch

from personal_db.config import Config
from personal_db.daemon import server as ds


def test_start_periodic_sync_invokes_sync_due_repeatedly(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def fake_sync_due(c):
        calls.append(time.time())
        return {}

    stop = threading.Event()
    with patch("personal_db.daemon.server.sync_due", side_effect=fake_sync_due):
        thread = ds.start_periodic_sync(cfg, interval_seconds=0.05, stop_event=stop)
        time.sleep(0.18)  # enough for ~3 ticks
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, f"expected periodic ticks, got {len(calls)}"
    assert not thread.is_alive()


def test_start_periodic_sync_swallows_errors_and_continues(tmp_root, caplog):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def flaky(c):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {}

    stop = threading.Event()
    with (
        caplog.at_level("ERROR", logger="personal_db.daemon"),
        patch("personal_db.daemon.server.sync_due", side_effect=flaky),
    ):
        thread = ds.start_periodic_sync(cfg, interval_seconds=0.05, stop_event=stop)
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, "thread should keep going after a single error"
    assert any("periodic sync_due failed" in rec.message for rec in caplog.records), \
        "the swallowed exception should have been logged"


def test_start_periodic_enrichments_invokes_runner_repeatedly(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def fake_run_due(c, *, limit, lease_seconds):
        calls.append((c, limit, lease_seconds, time.time()))
        return {"ran": 0}

    stop = threading.Event()
    with patch(
        "personal_db.daemon.server.run_due_finance_receipt_jobs",
        side_effect=fake_run_due,
    ):
        thread = ds.start_periodic_enrichments(
            cfg,
            interval_seconds=0.05,
            batch_size=7,
            lease_seconds=123,
            stop_event=stop,
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, f"expected periodic ticks, got {len(calls)}"
    assert all(call[0] is cfg for call in calls)
    assert all(call[1] == 7 for call in calls)
    assert all(call[2] == 123 for call in calls)
    assert not thread.is_alive()


def test_start_periodic_enrichments_swallows_errors_and_continues(tmp_root, caplog):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def flaky(c, *, limit, lease_seconds):
        calls.append((c, limit, lease_seconds))
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {"ran": 0}

    stop = threading.Event()
    with (
        caplog.at_level("ERROR", logger="personal_db.daemon"),
        patch(
            "personal_db.daemon.server.run_due_finance_receipt_jobs",
            side_effect=flaky,
        ),
    ):
        thread = ds.start_periodic_enrichments(
            cfg,
            interval_seconds=0.05,
            stop_event=stop,
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, "thread should keep going after a single error"
    assert any("periodic enrichment jobs failed" in rec.message for rec in caplog.records), \
        "the swallowed exception should have been logged"


def test_start_periodic_finance_receipt_enqueue_invokes_producer_repeatedly(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def fake_enqueue(c, *, limit, window_days, scope, stale_after_days, force):
        calls.append((c, limit, window_days, scope, stale_after_days, force, time.time()))
        return {"enqueued": 0}

    stop = threading.Event()
    with patch(
        "personal_db.daemon.server.enqueue_missing_receipt_enrichments",
        side_effect=fake_enqueue,
    ):
        thread = ds.start_periodic_finance_receipt_enqueue(
            cfg,
            interval_seconds=0.05,
            limit=17,
            window_days=3,
            stale_after_days=30,
            scope="Inbox",
            stop_event=stop,
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, f"expected periodic ticks, got {len(calls)}"
    assert all(call[0] is cfg for call in calls)
    assert all(call[1] == 17 for call in calls)
    assert all(call[2] == 3 for call in calls)
    assert all(call[3] == "Inbox" for call in calls)
    assert all(call[4] == 30 for call in calls)
    assert all(call[5] is False for call in calls)
    assert not thread.is_alive()


def test_start_periodic_finance_receipt_enqueue_swallows_errors_and_continues(
    tmp_root,
    caplog,
):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def flaky(c, *, limit, window_days, scope, stale_after_days, force):
        calls.append((c, limit, window_days, scope, stale_after_days, force))
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {"enqueued": 0}

    stop = threading.Event()
    with (
        caplog.at_level("ERROR", logger="personal_db.daemon"),
        patch(
            "personal_db.daemon.server.enqueue_missing_receipt_enrichments",
            side_effect=flaky,
        ),
    ):
        thread = ds.start_periodic_finance_receipt_enqueue(
            cfg,
            interval_seconds=0.05,
            stop_event=stop,
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, "thread should keep going after a single error"
    assert any("periodic finance receipt enqueue failed" in rec.message for rec in caplog.records), \
        "the swallowed exception should have been logged"


def test_start_periodic_finance_receipt_v1_enqueue_invokes_producer_repeatedly(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def fake_enqueue(
        c,
        *,
        limit,
        window_days,
        scope,
        max_threads,
        max_candidate_threads,
        stale_after_days,
        force,
    ):
        calls.append(
            (
                c,
                limit,
                window_days,
                scope,
                max_threads,
                max_candidate_threads,
                stale_after_days,
                force,
                time.time(),
            )
        )
        return {"enqueued": 0}

    stop = threading.Event()
    with patch(
        "personal_db.daemon.server.enqueue_missing_receipt_v1_enrichments",
        side_effect=fake_enqueue,
    ):
        thread = ds.start_periodic_finance_receipt_v1_enqueue(
            cfg,
            interval_seconds=0.05,
            limit=11,
            window_days=4,
            max_threads=2,
            max_candidate_threads=17,
            stale_after_days=14,
            scope="Inbox",
            stop_event=stop,
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, f"expected periodic ticks, got {len(calls)}"
    assert all(call[0] is cfg for call in calls)
    assert all(call[1] == 11 for call in calls)
    assert all(call[2] == 4 for call in calls)
    assert all(call[3] == "Inbox" for call in calls)
    assert all(call[4] == 2 for call in calls)
    assert all(call[5] == 17 for call in calls)
    assert all(call[6] == 14 for call in calls)
    assert all(call[7] is False for call in calls)
    assert not thread.is_alive()


def test_start_periodic_finance_receipt_v1_enqueue_swallows_errors_and_continues(
    tmp_root,
    caplog,
):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def flaky(
        c,
        *,
        limit,
        window_days,
        scope,
        max_threads,
        max_candidate_threads,
        stale_after_days,
        force,
    ):
        calls.append(
            (c, limit, window_days, scope, max_threads, max_candidate_threads, stale_after_days, force)
        )
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {"enqueued": 0}

    stop = threading.Event()
    with (
        caplog.at_level("ERROR", logger="personal_db.daemon"),
        patch(
            "personal_db.daemon.server.enqueue_missing_receipt_v1_enrichments",
            side_effect=flaky,
        ),
    ):
        thread = ds.start_periodic_finance_receipt_v1_enqueue(
            cfg,
            interval_seconds=0.05,
            stop_event=stop,
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, "thread should keep going after a single error"
    assert any("periodic finance receipt v1 enqueue failed" in rec.message for rec in caplog.records), \
        "the swallowed exception should have been logged"


def test_start_periodic_finance_receipt_v1_worker_invokes_runner_repeatedly(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def fake_run_due(c, *, limit, lease_seconds):
        calls.append((c, limit, lease_seconds, time.time()))
        return {"ran": 0}

    stop = threading.Event()
    with patch(
        "personal_db.daemon.server.run_due_finance_receipt_v1_jobs",
        side_effect=fake_run_due,
    ):
        thread = ds.start_periodic_finance_receipt_v1_worker(
            cfg,
            interval_seconds=0.05,
            batch_size=3,
            lease_seconds=456,
            stop_event=stop,
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, f"expected periodic ticks, got {len(calls)}"
    assert all(call[0] is cfg for call in calls)
    assert all(call[1] == 3 for call in calls)
    assert all(call[2] == 456 for call in calls)
    assert not thread.is_alive()


def test_start_periodic_finance_receipt_v1_worker_swallows_errors_and_continues(
    tmp_root,
    caplog,
):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def flaky(c, *, limit, lease_seconds):
        calls.append((c, limit, lease_seconds))
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {"ran": 0}

    stop = threading.Event()
    with (
        caplog.at_level("ERROR", logger="personal_db.daemon"),
        patch(
            "personal_db.daemon.server.run_due_finance_receipt_v1_jobs",
            side_effect=flaky,
        ),
    ):
        thread = ds.start_periodic_finance_receipt_v1_worker(
            cfg,
            interval_seconds=0.05,
            stop_event=stop,
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, "thread should keep going after a single error"
    assert any("periodic finance receipt v1 worker failed" in rec.message for rec in caplog.records), \
        "the swallowed exception should have been logged"


def test_enrichments_enabled_from_env(monkeypatch):
    monkeypatch.delenv(ds.ENRICHMENTS_ENABLED_ENV, raising=False)
    assert ds.enrichments_enabled_from_env() is False

    for enabled in ("1", "true", "yes", "on"):
        monkeypatch.setenv(ds.ENRICHMENTS_ENABLED_ENV, enabled)
        assert ds.enrichments_enabled_from_env() is True

    monkeypatch.setenv(ds.ENRICHMENTS_ENABLED_ENV, "0")
    assert ds.enrichments_enabled_from_env() is False


def test_finance_receipt_enqueue_enabled_from_env(monkeypatch):
    monkeypatch.delenv(ds.FINANCE_RECEIPT_ENQUEUE_ENABLED_ENV, raising=False)
    assert ds.finance_receipt_enqueue_enabled_from_env() is False

    monkeypatch.setenv(ds.FINANCE_RECEIPT_ENQUEUE_ENABLED_ENV, "yes")
    assert ds.finance_receipt_enqueue_enabled_from_env() is True

    monkeypatch.setenv(ds.FINANCE_RECEIPT_ENQUEUE_ENABLED_ENV, "false")
    assert ds.finance_receipt_enqueue_enabled_from_env() is False


def test_finance_receipt_v1_flags_from_env(monkeypatch):
    monkeypatch.delenv(ds.FINANCE_RECEIPT_V1_ENQUEUE_ENABLED_ENV, raising=False)
    monkeypatch.delenv(ds.FINANCE_RECEIPT_V1_WORKER_ENABLED_ENV, raising=False)
    assert ds.finance_receipt_v1_enqueue_enabled_from_env() is False
    assert ds.finance_receipt_v1_worker_enabled_from_env() is False

    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_ENQUEUE_ENABLED_ENV, "1")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_WORKER_ENABLED_ENV, "yes")
    assert ds.finance_receipt_v1_enqueue_enabled_from_env() is True
    assert ds.finance_receipt_v1_worker_enabled_from_env() is True

    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_ENQUEUE_ENABLED_ENV, "false")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_WORKER_ENABLED_ENV, "0")
    assert ds.finance_receipt_v1_enqueue_enabled_from_env() is False
    assert ds.finance_receipt_v1_worker_enabled_from_env() is False


def test_run_starts_enrichment_loop_when_env_enabled(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    sync_calls = []
    enrichment_calls = []

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self):
            return None

    def fake_start_sync(c, *, interval_seconds):
        sync_calls.append((c, interval_seconds))

    def fake_start_enrichments(c, *, interval_seconds, batch_size, lease_seconds):
        enrichment_calls.append((c, interval_seconds, batch_size, lease_seconds))

    monkeypatch.setenv(ds.ENRICHMENTS_ENABLED_ENV, "1")
    monkeypatch.setenv(ds.ENRICHMENT_INTERVAL_ENV, "42.5")
    monkeypatch.setenv(ds.ENRICHMENT_BATCH_SIZE_ENV, "9")
    monkeypatch.setenv(ds.ENRICHMENT_LEASE_SECONDS_ENV, "777")
    monkeypatch.setattr(ds, "start_periodic_sync", fake_start_sync)
    monkeypatch.setattr(ds, "start_periodic_enrichments", fake_start_enrichments)
    monkeypatch.setattr(ds.uvicorn, "Server", FakeServer)

    ds.run(cfg, port=9876, interval_seconds=600)

    assert sync_calls == [(cfg, 600)]
    assert enrichment_calls == [(cfg, 42.5, 9, 777)]


def test_run_starts_finance_receipt_enqueue_loop_when_env_enabled(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    sync_calls = []
    enqueue_calls = []

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self):
            return None

    def fake_start_sync(c, *, interval_seconds):
        sync_calls.append((c, interval_seconds))

    def fake_start_enqueue(
        c,
        *,
        interval_seconds,
        limit,
        window_days,
        stale_after_days,
        scope,
    ):
        enqueue_calls.append((c, interval_seconds, limit, window_days, stale_after_days, scope))

    monkeypatch.setenv(ds.FINANCE_RECEIPT_ENQUEUE_ENABLED_ENV, "1")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_ENQUEUE_INTERVAL_ENV, "123.5")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_ENQUEUE_LIMIT_ENV, "44")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_WINDOW_DAYS_ENV, "6")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_STALE_AFTER_DAYS_ENV, "31")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_SCOPE_ENV, "Inbox")
    monkeypatch.setattr(ds, "start_periodic_sync", fake_start_sync)
    monkeypatch.setattr(ds, "start_periodic_finance_receipt_enqueue", fake_start_enqueue)
    monkeypatch.setattr(ds.uvicorn, "Server", FakeServer)

    ds.run(cfg, port=9876, interval_seconds=600)

    assert sync_calls == [(cfg, 600)]
    assert enqueue_calls == [(cfg, 123.5, 44, 6, 31, "Inbox")]


def test_run_starts_v1_enqueue_and_warns_for_deprecated_daemon_worker(
    tmp_root,
    monkeypatch,
    caplog,
):
    cfg = Config(root=tmp_root)
    sync_calls = []
    enqueue_calls = []
    worker_calls = []

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self):
            return None

    def fake_start_sync(c, *, interval_seconds):
        sync_calls.append((c, interval_seconds))

    def fake_start_enqueue(
        c,
        *,
        interval_seconds,
        limit,
        window_days,
        max_threads,
        max_candidate_threads,
        stale_after_days,
        scope,
    ):
        enqueue_calls.append(
            (
                c,
                interval_seconds,
                limit,
                window_days,
                max_threads,
                max_candidate_threads,
                stale_after_days,
                scope,
            )
        )

    def fake_start_worker(c, *, interval_seconds, batch_size, lease_seconds):
        worker_calls.append((c, interval_seconds, batch_size, lease_seconds))

    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_ENQUEUE_ENABLED_ENV, "1")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_WORKER_ENABLED_ENV, "1")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_ENQUEUE_INTERVAL_ENV, "123.5")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_ENQUEUE_LIMIT_ENV, "12")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_WINDOW_DAYS_ENV, "6")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_MAX_THREADS_ENV, "2")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_MAX_CANDIDATE_THREADS_ENV, "17")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_STALE_AFTER_DAYS_ENV, "31")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_SCOPE_ENV, "Inbox")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_WORKER_INTERVAL_ENV, "456.5")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_BATCH_SIZE_ENV, "3")
    monkeypatch.setenv(ds.FINANCE_RECEIPT_V1_LEASE_SECONDS_ENV, "789")
    monkeypatch.setattr(ds, "start_periodic_sync", fake_start_sync)
    monkeypatch.setattr(ds, "start_periodic_finance_receipt_v1_enqueue", fake_start_enqueue)
    monkeypatch.setattr(ds, "start_periodic_finance_receipt_v1_worker", fake_start_worker)
    monkeypatch.setattr(ds.uvicorn, "Server", FakeServer)

    with caplog.at_level("WARNING", logger="personal_db.daemon"):
        ds.run(cfg, port=9876, interval_seconds=600)

    assert sync_calls == [(cfg, 600)]
    assert enqueue_calls == [(cfg, 123.5, 12, 6, 2, 17, 31, "Inbox")]
    assert worker_calls == []
    assert any(ds.V1_WORKER_DEPRECATED_MESSAGE in rec.message for rec in caplog.records)
