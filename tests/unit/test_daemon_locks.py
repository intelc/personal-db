"""Tests for personal_db.services.daemon._locks — centralized per-tracker lock dispatch."""

from __future__ import annotations

import yaml

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.services.daemon._locks import sync_due_locked, sync_one_locked
from tests._validation_helpers import mark_valid


def _make_tracker(tmp_root, name="alpha", every="1h"):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": every},
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
    mark_valid(cfg, name)
    return cfg


def test_sync_one_locked_runs_tracker(tmp_root):
    """sync_one_locked should complete without error and record last_run."""
    import json

    cfg = _make_tracker(tmp_root, "alpha")
    sync_one_locked(cfg, "alpha")
    last_run = json.loads((tmp_root / "state" / "last_run.json").read_text())
    assert "alpha" in last_run


def test_sync_due_locked_routes_through_sync_one_locked(tmp_root):
    """sync_due_locked should call sync_one_locked (via the sync_one_fn hook)
    rather than the plain sync_one — verified by patching sync_one_locked in
    the _locks module and confirming it is invoked."""
    from unittest.mock import patch

    from personal_db.services.daemon import _locks

    cfg = _make_tracker(tmp_root, "beta")
    seen: list[str] = []

    original = _locks.sync_one_locked

    def spy(c, name):
        seen.append(name)
        original(c, name)

    # Patch the module-level reference used inside sync_due_locked.
    with patch.object(_locks, "sync_one_locked", side_effect=spy):
        results = sync_due_locked(cfg)

    assert "beta" in results
    assert results["beta"] == "ok"
    assert "beta" in seen, "sync_one_locked should have been called for due tracker"


def test_sync_due_locked_skips_not_due(tmp_root):
    """After a fresh sync, the same tracker should show 'skip' on the next call."""
    cfg = _make_tracker(tmp_root, "gamma")
    # First call — should run and record last_run
    r1 = sync_due_locked(cfg)
    assert r1.get("gamma") == "ok"
    # Second call immediately after — should be skipped (not yet due)
    r2 = sync_due_locked(cfg)
    assert r2.get("gamma") == "skip"
