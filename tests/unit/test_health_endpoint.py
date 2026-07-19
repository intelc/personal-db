"""Tests for the daemon health payload's `repeated_sync_failures` field.

Covers `services/ui/builtin_viz.py::repeated_failure_trackers` directly (unit
level) and its wiring into `GET /api/v1/health` (routes/daemon level).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.services.daemon.http import build_app
from personal_db.services.ui.builtin_viz import repeated_failure_trackers
from tests._daemon_auth import auth_headers


def _write_errors(cfg: Config, records: list[dict]) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    err_path = cfg.state_dir / "sync_errors.jsonl"
    with err_path.open("a") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _write_last_run(cfg: Config, runs: dict[str, str]) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "last_run.json").write_text(json.dumps(runs))


def _err(tracker: str, ts: datetime) -> dict:
    return {"tracker": tracker, "ts": ts.isoformat(), "error": "boom", "tb": ""}


def test_two_errors_in_window_are_listed(tmp_path):
    cfg = Config(root=tmp_path)
    now = datetime.now(UTC)
    _write_errors(
        cfg,
        [
            _err("flaky", now - timedelta(hours=2)),
            _err("flaky", now - timedelta(minutes=5)),
        ],
    )
    assert repeated_failure_trackers(cfg) == ["flaky"]


def test_success_after_errors_clears_it(tmp_path):
    cfg = Config(root=tmp_path)
    now = datetime.now(UTC)
    _write_errors(
        cfg,
        [
            _err("recovered", now - timedelta(hours=2)),
            _err("recovered", now - timedelta(hours=1)),
        ],
    )
    _write_last_run(cfg, {"recovered": now.isoformat()})
    assert repeated_failure_trackers(cfg) == []


def test_single_error_is_not_listed(tmp_path):
    cfg = Config(root=tmp_path)
    now = datetime.now(UTC)
    _write_errors(cfg, [_err("onceoff", now - timedelta(minutes=5))])
    assert repeated_failure_trackers(cfg) == []


def test_errors_outside_window_do_not_count(tmp_path):
    cfg = Config(root=tmp_path)
    now = datetime.now(UTC)
    _write_errors(
        cfg,
        [
            _err("stale", now - timedelta(hours=48)),
            _err("stale", now - timedelta(hours=30)),
        ],
    )
    assert repeated_failure_trackers(cfg) == []


def test_no_sync_errors_file_returns_empty(tmp_path):
    cfg = Config(root=tmp_path)
    assert repeated_failure_trackers(cfg) == []


def test_health_route_includes_repeated_sync_failures(tmp_path):
    cfg = Config(root=tmp_path)
    init_db(cfg.db_path)
    now = datetime.now(UTC)
    _write_errors(
        cfg,
        [
            _err("flaky", now - timedelta(hours=2)),
            _err("flaky", now - timedelta(minutes=5)),
        ],
    )
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["repeated_sync_failures"] == ["flaky"]


def test_health_route_repeated_sync_failures_empty_when_healthy(tmp_path):
    cfg = Config(root=tmp_path)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["repeated_sync_failures"] == []
