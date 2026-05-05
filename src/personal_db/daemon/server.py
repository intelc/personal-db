"""Daemon orchestrator: periodic sync loop + uvicorn server."""

from __future__ import annotations

import logging
import threading

import uvicorn

from personal_db.config import Config
from personal_db.daemon._locks import sync_due_locked as sync_due
from personal_db.daemon.http import build_app

log = logging.getLogger("personal_db.daemon")


def start_periodic_sync(
    cfg: Config,
    interval_seconds: float = 600,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Spawn a daemon thread that runs sync_due every interval_seconds.

    Errors inside sync_due are caught and logged so a single tracker failure
    can't take down the loop.
    """
    stop = stop_event or threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                sync_due(cfg)
            except Exception:  # noqa: BLE001
                log.exception("periodic sync_due failed")
            # Event.wait returns immediately when set; otherwise sleeps until timeout.
            stop.wait(timeout=interval_seconds)

    t = threading.Thread(target=_loop, daemon=True, name="personal-db-periodic-sync")
    t.start()
    return t


def run(cfg: Config, port: int = 8765, interval_seconds: float = 600) -> None:
    """Run the daemon: start the periodic loop, then serve HTTP on 127.0.0.1:port."""
    start_periodic_sync(cfg, interval_seconds=interval_seconds)
    app = build_app(cfg)
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="info", access_log=False)
    uvicorn.Server(config).run()
