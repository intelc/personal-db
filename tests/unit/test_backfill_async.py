"""Tests for personal_db.backfill.start_async."""

from __future__ import annotations

import time

from personal_db import backfill as backfill_mod
from personal_db.config import Config


def test_start_async_creates_log_file(tmp_path):
    """start_async returns a log path under <root>/state/ and the file exists."""
    cfg = Config(root=tmp_path / "personal_db")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    log_path = backfill_mod.start_async(cfg, "habits")
    assert log_path == cfg.state_dir / "backfill_habits.log"
    assert log_path.exists()


def test_start_async_returns_immediately(tmp_path):
    """The subprocess is detached — start_async must not block on it."""
    cfg = Config(root=tmp_path / "personal_db")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    backfill_mod.start_async(cfg, "habits")
    elapsed = time.monotonic() - t0
    # Spawning a Python interpreter is dominated by startup overhead but should
    # be well under a second on any dev machine. We're generous here since the
    # subprocess itself is the slow part and we DON'T want to wait for it.
    assert elapsed < 2.0, f"start_async blocked for {elapsed:.2f}s"
