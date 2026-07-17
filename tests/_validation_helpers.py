"""Test helper: stamp a hand-built tracker dir as validated.

Phase 2b makes `sync_one`/`backfill_one` refuse to run a tracker whose files
have no matching `tracker_validation` stamp (core/validation.py). Tests that
install a real bundled tracker via `install_template`/`update_template` get
auto-stamped for free (see core/installer.py). Tests that hand-build a
synthetic tracker directory need to stamp it themselves before calling
sync_one/backfill_one directly — that's what this helper is for.
"""

from __future__ import annotations

from personal_db.core.config import Config
from personal_db.core.validation import compute_files_hash, record_validation


def mark_valid(cfg: Config, name: str) -> None:
    tracker_dir = cfg.trackers_dir / name
    record_validation(cfg, name, compute_files_hash(tracker_dir))
