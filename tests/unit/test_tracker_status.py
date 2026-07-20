"""Unit tests for services/ui/builtin_viz.py's tracker_status_map and the
staleness rule it implements (surfaced on /health, /t/<tracker>, and the
dashboard's Tracker Health card).

Fixtures are built the same lightweight way as tests/unit/test_sync.py's
`_make_tracker_dir`: write a manifest.yaml (+ minimal schema.sql/ingest.py so
the tracker dir looks installed) directly under cfg.trackers_dir, rather than
running the real installer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import yaml

from personal_db.core.config import Config
from personal_db.services.ui.builtin_viz import build_health_page_data, tracker_status_map


def _make_tracker_dir(cfg: Config, name: str, schedule_every: str | None = "1h") -> None:
    d = cfg.trackers_dir / name
    d.mkdir(parents=True)
    manifest: dict = {
        "name": name,
        "description": "test",
        "permission_type": "none",
        "setup_steps": [],
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
    if schedule_every is not None:
        manifest["schedule"] = {"every": schedule_every}
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    (d / "schema.sql").write_text(
        f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, ts TEXT);"
    )
    (d / "ingest.py").write_text("def sync(t):\n    pass\n")


def _write_last_run(cfg: Config, runs: dict[str, str]) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "last_run.json").write_text(json.dumps(runs))


def _write_error(cfg: Config, tracker: str, ts: datetime) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with (cfg.state_dir / "sync_errors.jsonl").open("a") as f:
        f.write(json.dumps({"tracker": tracker, "ts": ts.isoformat(), "error": "boom", "tb": ""}) + "\n")


# -- `every`-scheduled trackers: stale past max(3x interval, 24h) ------------


def test_every_schedule_not_stale_within_threshold(tmp_path):
    """1h schedule -> 3x = 3h, but the 24h floor applies, so 6h ago is not stale."""
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "hourly", schedule_every="1h")
    now = datetime.now(UTC)
    _write_last_run(cfg, {"hourly": (now - timedelta(hours=6)).isoformat()})

    status = tracker_status_map(cfg)["hourly"]
    assert status["stale"] is False
    assert status["error"] is None


def test_every_schedule_stale_past_24h_floor(tmp_path):
    """1h schedule: 3x interval is only 3h, but the 24h floor means we don't
    flag stale until >24h, not >3h."""
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "hourly", schedule_every="1h")
    now = datetime.now(UTC)
    _write_last_run(cfg, {"hourly": (now - timedelta(hours=25)).isoformat()})

    assert tracker_status_map(cfg)["hourly"]["stale"] is True


def test_every_schedule_stale_past_3x_interval_when_longer_than_24h(tmp_path):
    """12h schedule -> 3x = 36h (> the 24h floor), so 30h ago is not yet stale
    but 40h ago is."""
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "twice_daily", schedule_every="12h")
    now = datetime.now(UTC)

    _write_last_run(cfg, {"twice_daily": (now - timedelta(hours=30)).isoformat()})
    assert tracker_status_map(cfg)["twice_daily"]["stale"] is False

    _write_last_run(cfg, {"twice_daily": (now - timedelta(hours=40)).isoformat()})
    assert tracker_status_map(cfg)["twice_daily"]["stale"] is True


# -- manual (no schedule) trackers: stale when missing or older than 7 days --


def test_manual_tracker_stale_when_never_synced(tmp_path):
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "manual_tracker", schedule_every=None)

    assert tracker_status_map(cfg)["manual_tracker"]["stale"] is True
    assert tracker_status_map(cfg)["manual_tracker"]["last_sync_age"] is None


def test_manual_tracker_not_stale_within_7_days(tmp_path):
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "manual_tracker", schedule_every=None)
    now = datetime.now(UTC)
    _write_last_run(cfg, {"manual_tracker": (now - timedelta(days=6)).isoformat()})

    assert tracker_status_map(cfg)["manual_tracker"]["stale"] is False


def test_manual_tracker_stale_past_7_days(tmp_path):
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "manual_tracker", schedule_every=None)
    now = datetime.now(UTC)
    _write_last_run(cfg, {"manual_tracker": (now - timedelta(days=8)).isoformat()})

    assert tracker_status_map(cfg)["manual_tracker"]["stale"] is True


# -- error wins over stale ----------------------------------------------------


def test_active_error_suppresses_stale_flag(tmp_path):
    """A tracker with a broken OAuth (repeated failures, no successful sync in
    16 days) should show error=... but stale should stay False -- the error
    is the more specific signal, so it shouldn't also double-flag as stale."""
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "broken_oauth", schedule_every="6h")
    now = datetime.now(UTC)
    old_success = now - timedelta(days=16)
    _write_last_run(cfg, {"broken_oauth": old_success.isoformat()})
    _write_error(cfg, "broken_oauth", now - timedelta(hours=1))

    status = tracker_status_map(cfg)["broken_oauth"]
    assert status["error"] is not None
    assert status["error"]["first_line"] == "boom"
    assert status["stale"] is False


def test_resolved_error_does_not_suppress_stale(tmp_path):
    """An old, already-resolved error (success came after it) shouldn't hide
    a genuinely stale tracker."""
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "recovered_but_stale", schedule_every=None)
    now = datetime.now(UTC)
    _write_error(cfg, "recovered_but_stale", now - timedelta(days=20))
    _write_last_run(cfg, {"recovered_but_stale": (now - timedelta(days=10)).isoformat()})

    status = tracker_status_map(cfg)["recovered_but_stale"]
    assert status["error"] is None
    assert status["stale"] is True


# -- build_health_page_data rows carry the same `stale` flag -----------------


def test_build_health_page_data_rows_include_stale_flag(tmp_path):
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "hourly", schedule_every="1h")
    now = datetime.now(UTC)
    _write_last_run(cfg, {"hourly": (now - timedelta(hours=30)).isoformat()})

    data = build_health_page_data(cfg, uptime_seconds=0, app_version="test", db_user_version=1)
    row = next(r for r in data["rows"] if r["slug"] == "hourly")
    assert row["stale"] is True
    assert row["error"] is None


def test_build_health_page_data_rows_not_stale_when_recent(tmp_path):
    cfg = Config(root=tmp_path)
    _make_tracker_dir(cfg, "hourly", schedule_every="1h")
    now = datetime.now(UTC)
    _write_last_run(cfg, {"hourly": (now - timedelta(minutes=10)).isoformat()})

    data = build_health_page_data(cfg, uptime_seconds=0, app_version="test", db_user_version=1)
    row = next(r for r in data["rows"] if r["slug"] == "hourly")
    assert row["stale"] is False
