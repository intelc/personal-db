"""Daemon orchestrator: periodic sync loop + generic background-job scheduler + uvicorn server."""

from __future__ import annotations

import logging
import threading

import uvicorn

from personal_db.core.action_log import log_action_result, log_action_start
from personal_db.core.background_jobs import DeclaredBackgroundJob, discover_background_jobs
from personal_db.core.config import Config
from personal_db.core.entrypoints import load_entrypoint
from personal_db.core.intervals import parse_every
from personal_db.core.runtime_env import activate_lib_dir
from personal_db.services.daemon._locks import sync_due_locked as sync_due
from personal_db.services.daemon.http import build_app

log = logging.getLogger("personal_db.services.daemon")


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
            except Exception:
                log.exception("periodic sync_due failed")
            # Event.wait returns immediately when set; otherwise sleeps until timeout.
            stop.wait(timeout=interval_seconds)

    t = threading.Thread(target=_loop, daemon=True, name="personal-db-periodic-sync")
    t.start()
    return t


def start_periodic_background_job(
    cfg: Config,
    job: DeclaredBackgroundJob,
    *,
    interval_seconds: float | None = None,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Spawn a daemon thread that runs one declared background job at its cadence.

    The job's entrypoint is resolved fresh on every tick (via
    core.entrypoints.load_entrypoint) so edits to a tracker/app's job module
    take effect without a daemon restart. Errors are caught and logged per-job
    so one failing job can't take down the daemon or other jobs.

    `interval_seconds` overrides the cadence parsed from `job.spec.every`;
    tests use this to avoid `every`'s whole-second/minute granularity.
    """
    stop = stop_event or threading.Event()
    interval_seconds = (
        interval_seconds if interval_seconds is not None else parse_every(job.spec.every).total_seconds()
    )

    def _loop() -> None:
        while not stop.is_set():
            log_id = log_action_start(
                cfg,
                surface="background_job",
                extension=f"{job.extension_kind}:{job.extension_name}",
                action=job.spec.name,
            )
            try:
                func = load_entrypoint(
                    job.base_dir,
                    job.spec.entrypoint,
                    modname_prefix=f"pdb_job_{job.extension_kind}_{job.extension_name}",
                )
                result = func(cfg)
                log.info("background job %s completed: %s", job.qualified_name, result)
                log_action_result(cfg, log_id, "ok")
            except Exception as exc:
                log.exception("background job %s failed", job.qualified_name)
                log_action_result(cfg, log_id, f"error: {exc}")
            stop.wait(timeout=interval_seconds)

    t = threading.Thread(
        target=_loop,
        daemon=True,
        name=f"personal-db-job-{job.extension_kind}-{job.extension_name}-{job.spec.name}",
    )
    t.start()
    return t


def start_declared_background_jobs(
    cfg: Config,
    *,
    stop_event: threading.Event | None = None,
) -> list[threading.Thread]:
    """Discover installed trackers/apps and schedule every declared job.

    Discovery happens once (at daemon start); each job gets its own thread
    ticking at its own `every` cadence, mirroring start_periodic_sync.
    """
    jobs = discover_background_jobs(cfg)
    threads = []
    for job in jobs:
        try:
            threads.append(start_periodic_background_job(cfg, job, stop_event=stop_event))
        except Exception:
            log.exception("failed to schedule background job %s", job.qualified_name)
    return threads


def run(cfg: Config, port: int = 8765, interval_seconds: float = 600) -> None:
    """Run the daemon: start the periodic loop + declared jobs, then serve HTTP."""
    # The daemon holds the macOS Full Disk Access grant and runs every
    # tracker sync, so it's the one process that must see <root>/lib on
    # sys.path (see core/runtime_env.py for why this can't just live in the
    # sealed bundle's site-packages).
    activate_lib_dir(cfg)
    start_periodic_sync(cfg, interval_seconds=interval_seconds)
    start_declared_background_jobs(cfg)
    app = build_app(cfg, port=port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="info", access_log=False)
    uvicorn.Server(config).run()
