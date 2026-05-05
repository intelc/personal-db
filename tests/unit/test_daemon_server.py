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
    with caplog.at_level("ERROR", logger="personal_db.daemon"):
        with patch("personal_db.daemon.server.sync_due", side_effect=flaky):
            thread = ds.start_periodic_sync(cfg, interval_seconds=0.05, stop_event=stop)
            time.sleep(0.18)
            stop.set()
            thread.join(timeout=2.0)

    assert len(calls) >= 2, "thread should keep going after a single error"
    assert any("periodic sync_due failed" in rec.message for rec in caplog.records), \
        "the swallowed exception should have been logged"
