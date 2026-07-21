from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.core.sync import sync_due, sync_one
from personal_db.core.sync_backoff import (
    PAUSE_AFTER,
    blocked_reason,
    record_failure,
    record_success,
    retry_delay,
    tracker_state,
)
from tests._validation_helpers import mark_valid


def _make_tracker_dir(cfg: Config, name: str, schedule_every: str = "1h", *, fails: bool = False):
    """Minimal valid tracker whose `sync()` either succeeds or raises,
    mirroring `test_sync.py`'s helper but with a `fails` switch so the
    backoff-recording tests can drive real failures through `sync_one`."""
    d = cfg.trackers_dir / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": schedule_every},
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
    if fails:
        (d / "ingest.py").write_text(
            "def backfill(t, start, end):\n    pass\n"
            "def sync(t):\n    raise RuntimeError('boom')\n"
        )
    else:
        (d / "ingest.py").write_text(
            "def backfill(t, start, end):\n    pass\n"
            "def sync(t):\n    t.upsert(t.name, [{'id': 's1', 'ts': '2026-04-25'}], key=['id'])\n"
        )
    mark_valid(cfg, name)
    return d


# --- retry_delay / ladder ----------------------------------------------------


def test_retry_delay_ladder():
    assert retry_delay(1) == timedelta(0)
    assert retry_delay(2) == timedelta(0)
    assert retry_delay(3) == timedelta(minutes=30)
    assert retry_delay(4) == timedelta(hours=2)
    assert retry_delay(5) == timedelta(hours=8)


def test_retry_delay_paused_at_threshold():
    assert PAUSE_AFTER == 6
    assert retry_delay(6) is None
    assert retry_delay(7) is None
    assert retry_delay(100) is None


# --- record_failure / record_success -----------------------------------------


def test_record_failure_increments_and_pauses_at_threshold(tmp_root):
    cfg = Config(root=tmp_root)
    for _ in range(PAUSE_AFTER - 1):
        record_failure(cfg, "demo")
    state = tracker_state(cfg, "demo")
    assert state["consecutive_failures"] == PAUSE_AFTER - 1
    assert state["paused"] is False

    record_failure(cfg, "demo")
    state = tracker_state(cfg, "demo")
    assert state["consecutive_failures"] == PAUSE_AFTER
    assert state["paused"] is True


def test_record_success_clears_entry(tmp_root):
    cfg = Config(root=tmp_root)
    record_failure(cfg, "demo")
    record_failure(cfg, "demo")
    assert tracker_state(cfg, "demo") is not None

    record_success(cfg, "demo")
    assert tracker_state(cfg, "demo") is None


def test_record_success_on_absent_tracker_is_a_noop(tmp_root):
    cfg = Config(root=tmp_root)
    record_success(cfg, "never_failed")  # must not raise
    assert tracker_state(cfg, "never_failed") is None


# --- blocked_reason -----------------------------------------------------------


def test_blocked_reason_absent_tracker(tmp_root):
    cfg = Config(root=tmp_root)
    assert blocked_reason(cfg, "demo", datetime.now(UTC)) is None


def test_blocked_reason_fresh_failures_no_delay(tmp_root):
    """1-2 consecutive failures should never block -- normal cadence."""
    cfg = Config(root=tmp_root)
    record_failure(cfg, "demo")
    assert blocked_reason(cfg, "demo", datetime.now(UTC)) is None
    record_failure(cfg, "demo")
    assert blocked_reason(cfg, "demo", datetime.now(UTC)) is None


def test_blocked_reason_backoff_window(tmp_root):
    cfg = Config(root=tmp_root)
    for _ in range(3):
        record_failure(cfg, "demo")  # consecutive_failures == 3 -> 30 min delay
    now = datetime.now(UTC)
    assert blocked_reason(cfg, "demo", now) == "backoff"
    # Still inside the window
    assert blocked_reason(cfg, "demo", now + timedelta(minutes=10)) == "backoff"
    # Past the 30-minute delay, no longer blocked
    assert blocked_reason(cfg, "demo", now + timedelta(minutes=31)) is None


def test_blocked_reason_paused(tmp_root):
    cfg = Config(root=tmp_root)
    for _ in range(PAUSE_AFTER):
        record_failure(cfg, "demo")
    now = datetime.now(UTC)
    assert blocked_reason(cfg, "demo", now) == "paused"
    # Paused never un-blocks with the passage of time alone.
    assert blocked_reason(cfg, "demo", now + timedelta(days=365)) == "paused"


# --- sync_due gating -----------------------------------------------------------


def test_sync_due_skips_paused_and_reports_status(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(cfg, "broken", schedule_every="1h")
    _make_tracker_dir(cfg, "healthy", schedule_every="1h")

    for _ in range(PAUSE_AFTER):
        record_failure(cfg, "broken")

    called: list[str] = []

    def fake_sync_one(c, name):
        called.append(name)
        sync_one(c, name)

    results = sync_due(cfg, sync_one_fn=fake_sync_one)
    assert results["broken"] == "paused"
    assert results["healthy"] == "ok"
    assert called == ["healthy"]
    # A paused tracker being skipped must not itself increment the failure
    # counter -- it should be left exactly as it was.
    assert tracker_state(cfg, "broken")["consecutive_failures"] == PAUSE_AFTER


def test_sync_due_skips_tracker_in_backoff_window(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(cfg, "flaky", schedule_every="1h")

    for _ in range(3):  # consecutive_failures == 3 -> 30 min delay
        record_failure(cfg, "flaky")

    called: list[str] = []

    def fake_sync_one(c, name):
        called.append(name)
        sync_one(c, name)

    results = sync_due(cfg, sync_one_fn=fake_sync_one)
    assert results["flaky"] == "backoff"
    assert called == []


# --- sync_one always runs / resume mechanism -----------------------------------


def test_sync_one_records_failure_on_each_call(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(cfg, "broken", schedule_every="1h", fails=True)

    for i in range(1, PAUSE_AFTER + 1):
        try:
            sync_one(cfg, "broken")
        except RuntimeError:
            pass
        state = tracker_state(cfg, "broken")
        assert state["consecutive_failures"] == i
        assert state["paused"] == (i >= PAUSE_AFTER)


def test_sync_one_runs_even_when_paused_and_success_unpauses(tmp_root):
    """sync_one is the manual/API entry point and is never gated by backoff --
    a paused tracker must still run (and can resume) via a direct sync_one
    call, which is the only resume mechanism."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(cfg, "demo", schedule_every="1h")

    for _ in range(PAUSE_AFTER):
        record_failure(cfg, "demo")
    assert tracker_state(cfg, "demo")["paused"] is True

    # sync_one must still run (no gate) and its success clears the entry.
    sync_one(cfg, "demo")
    assert tracker_state(cfg, "demo") is None
    assert blocked_reason(cfg, "demo", datetime.now(UTC)) is None
