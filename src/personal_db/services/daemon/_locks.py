"""Per-tracker locks shared across daemon sync entry points.

Lives in its own module so both ``daemon.http`` (HTTP routes) and
``daemon.server`` (periodic loop) can call the same locking helpers
without circular imports.
"""

from __future__ import annotations

import threading
from collections import defaultdict

from personal_db.core.config import Config

_tracker_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def sync_one_locked(cfg: Config, tracker: str) -> None:
    from personal_db.core.sync import sync_one

    with _tracker_locks[tracker]:
        sync_one(cfg, tracker)


def backfill_locked(cfg: Config, tracker: str, start: str | None, end: str | None) -> None:
    from personal_db.core.sync import backfill_one

    with _tracker_locks[tracker]:
        backfill_one(cfg, tracker, start, end)


def sync_due_locked(cfg: Config) -> dict[str, str]:
    """Locked equivalent of :func:`personal_db.core.sync.sync_due`.

    Routes each per-tracker call through :func:`sync_one_locked` so the
    periodic loop and explicit HTTP requests cannot race on the same
    tracker's pagination cursor.
    """
    from personal_db.core.sync import sync_due

    return sync_due(cfg, sync_one_fn=sync_one_locked)
