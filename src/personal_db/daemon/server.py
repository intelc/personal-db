"""Daemon orchestrator: periodic sync loop + uvicorn server."""

from __future__ import annotations

import logging
import os
import threading

import uvicorn

from personal_db.config import Config
from personal_db.daemon._locks import sync_due_locked as sync_due
from personal_db.daemon.http import build_app
from personal_db.enrichments.finance import (
    enqueue_missing_receipt_enrichments,
    enqueue_missing_receipt_v1_enrichments,
    run_due_finance_receipt_jobs,
    run_due_finance_receipt_v1_jobs,
)

log = logging.getLogger("personal_db.daemon")

ENRICHMENTS_ENABLED_ENV = "PERSONAL_DB_ENRICHMENTS_ENABLED"
ENRICHMENT_INTERVAL_ENV = "PERSONAL_DB_ENRICHMENT_INTERVAL_SECONDS"
ENRICHMENT_BATCH_SIZE_ENV = "PERSONAL_DB_ENRICHMENT_BATCH_SIZE"
ENRICHMENT_LEASE_SECONDS_ENV = "PERSONAL_DB_ENRICHMENT_LEASE_SECONDS"
FINANCE_RECEIPT_ENQUEUE_ENABLED_ENV = "PERSONAL_DB_FINANCE_RECEIPT_ENQUEUE_ENABLED"
FINANCE_RECEIPT_ENQUEUE_INTERVAL_ENV = "PERSONAL_DB_FINANCE_RECEIPT_ENQUEUE_INTERVAL_SECONDS"
FINANCE_RECEIPT_ENQUEUE_LIMIT_ENV = "PERSONAL_DB_FINANCE_RECEIPT_ENQUEUE_LIMIT"
FINANCE_RECEIPT_WINDOW_DAYS_ENV = "PERSONAL_DB_FINANCE_RECEIPT_WINDOW_DAYS"
FINANCE_RECEIPT_STALE_AFTER_DAYS_ENV = "PERSONAL_DB_FINANCE_RECEIPT_STALE_AFTER_DAYS"
FINANCE_RECEIPT_SCOPE_ENV = "PERSONAL_DB_FINANCE_RECEIPT_SCOPE"
FINANCE_RECEIPT_V1_ENQUEUE_ENABLED_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_ENQUEUE_ENABLED"
FINANCE_RECEIPT_V1_WORKER_ENABLED_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_WORKER_ENABLED"
FINANCE_RECEIPT_V1_ENQUEUE_INTERVAL_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_ENQUEUE_INTERVAL_SECONDS"
FINANCE_RECEIPT_V1_ENQUEUE_LIMIT_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_ENQUEUE_LIMIT"
FINANCE_RECEIPT_V1_WINDOW_DAYS_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_WINDOW_DAYS"
FINANCE_RECEIPT_V1_MAX_THREADS_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_MAX_THREADS"
FINANCE_RECEIPT_V1_MAX_CANDIDATE_THREADS_ENV = (
    "PERSONAL_DB_FINANCE_RECEIPT_V1_MAX_CANDIDATE_THREADS"
)
FINANCE_RECEIPT_V1_STALE_AFTER_DAYS_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_STALE_AFTER_DAYS"
FINANCE_RECEIPT_V1_SCOPE_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_SCOPE"
FINANCE_RECEIPT_V1_WORKER_INTERVAL_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_WORKER_INTERVAL_SECONDS"
FINANCE_RECEIPT_V1_BATCH_SIZE_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_BATCH_SIZE"
FINANCE_RECEIPT_V1_LEASE_SECONDS_ENV = "PERSONAL_DB_FINANCE_RECEIPT_V1_LEASE_SECONDS"
V1_WORKER_DEPRECATED_MESSAGE = (
    f"{FINANCE_RECEIPT_V1_WORKER_ENABLED_ENV} is ignored by the daemon. "
    "Use `personal-db worker install --kind finance-receipt-v1` to run v1 enrichments."
)


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


def start_periodic_enrichments(
    cfg: Config,
    interval_seconds: float = 600,
    batch_size: int = 5,
    lease_seconds: int = 300,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Spawn a daemon thread that drains due enrichment jobs.

    Jobs are claimed with leases in the queue layer; this loop only supplies the
    cadence and catches worker errors so enrichment can't take down the daemon.
    """
    stop = stop_event or threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                run_due_finance_receipt_jobs(
                    cfg,
                    limit=batch_size,
                    lease_seconds=lease_seconds,
                )
            except Exception:
                log.exception("periodic enrichment jobs failed")
            stop.wait(timeout=interval_seconds)

    t = threading.Thread(target=_loop, daemon=True, name="personal-db-periodic-enrichments")
    t.start()
    return t


def start_periodic_finance_receipt_enqueue(
    cfg: Config,
    interval_seconds: float = 3600,
    limit: int = 50,
    window_days: int = 7,
    stale_after_days: int | None = None,
    scope: str | None = None,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Spawn a daemon thread that queues missing or stale receipt enrichments."""
    stop = stop_event or threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                enqueue_missing_receipt_enrichments(
                    cfg,
                    limit=limit,
                    window_days=window_days,
                    scope=scope,
                    stale_after_days=stale_after_days,
                    force=False,
                )
            except Exception:
                log.exception("periodic finance receipt enqueue failed")
            stop.wait(timeout=interval_seconds)

    t = threading.Thread(target=_loop, daemon=True, name="personal-db-finance-receipt-enqueue")
    t.start()
    return t


def start_periodic_finance_receipt_v1_enqueue(
    cfg: Config,
    interval_seconds: float = 3600,
    limit: int = 20,
    window_days: int = 7,
    max_threads: int = 3,
    max_candidate_threads: int = 20,
    stale_after_days: int | None = None,
    scope: str | None = None,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Spawn a daemon thread that queues missing or stale v1 receipt enrichments."""
    stop = stop_event or threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                enqueue_missing_receipt_v1_enrichments(
                    cfg,
                    limit=limit,
                    window_days=window_days,
                    scope=scope,
                    max_threads=max_threads,
                    max_candidate_threads=max_candidate_threads,
                    stale_after_days=stale_after_days,
                    force=False,
                )
            except Exception:
                log.exception("periodic finance receipt v1 enqueue failed")
            stop.wait(timeout=interval_seconds)

    t = threading.Thread(target=_loop, daemon=True, name="personal-db-finance-receipt-v1-enqueue")
    t.start()
    return t


def start_periodic_finance_receipt_v1_worker(
    cfg: Config,
    interval_seconds: float = 600,
    batch_size: int = 2,
    lease_seconds: int = 600,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Spawn a daemon thread that drains due v1 receipt enrichment jobs."""
    stop = stop_event or threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                run_due_finance_receipt_v1_jobs(
                    cfg,
                    limit=batch_size,
                    lease_seconds=lease_seconds,
                )
            except Exception:
                log.exception("periodic finance receipt v1 worker failed")
            stop.wait(timeout=interval_seconds)

    t = threading.Thread(target=_loop, daemon=True, name="personal-db-finance-receipt-v1-worker")
    t.start()
    return t


def enrichments_enabled_from_env() -> bool:
    return _env_bool(ENRICHMENTS_ENABLED_ENV, default=False)


def finance_receipt_enqueue_enabled_from_env() -> bool:
    return _env_bool(FINANCE_RECEIPT_ENQUEUE_ENABLED_ENV, default=False)


def finance_receipt_v1_enqueue_enabled_from_env() -> bool:
    return _env_bool(FINANCE_RECEIPT_V1_ENQUEUE_ENABLED_ENV, default=False)


def finance_receipt_v1_worker_enabled_from_env() -> bool:
    return _env_bool(FINANCE_RECEIPT_V1_WORKER_ENABLED_ENV, default=False)


def run(cfg: Config, port: int = 8765, interval_seconds: float = 600) -> None:
    """Run the daemon: start the periodic loop, then serve HTTP on 127.0.0.1:port."""
    start_periodic_sync(cfg, interval_seconds=interval_seconds)
    if finance_receipt_enqueue_enabled_from_env():
        start_periodic_finance_receipt_enqueue(
            cfg,
            interval_seconds=_env_float(FINANCE_RECEIPT_ENQUEUE_INTERVAL_ENV, 3600),
            limit=_env_int(FINANCE_RECEIPT_ENQUEUE_LIMIT_ENV, 50),
            window_days=_env_int(FINANCE_RECEIPT_WINDOW_DAYS_ENV, 7),
            stale_after_days=_env_optional_int(FINANCE_RECEIPT_STALE_AFTER_DAYS_ENV),
            scope=_env_optional_str(FINANCE_RECEIPT_SCOPE_ENV),
        )
    if finance_receipt_v1_enqueue_enabled_from_env():
        start_periodic_finance_receipt_v1_enqueue(
            cfg,
            interval_seconds=_env_float(FINANCE_RECEIPT_V1_ENQUEUE_INTERVAL_ENV, 3600),
            limit=_env_int(FINANCE_RECEIPT_V1_ENQUEUE_LIMIT_ENV, 20),
            window_days=_env_int(FINANCE_RECEIPT_V1_WINDOW_DAYS_ENV, 7),
            max_threads=_env_int(FINANCE_RECEIPT_V1_MAX_THREADS_ENV, 3),
            max_candidate_threads=_env_int(FINANCE_RECEIPT_V1_MAX_CANDIDATE_THREADS_ENV, 20),
            stale_after_days=_env_optional_int(FINANCE_RECEIPT_V1_STALE_AFTER_DAYS_ENV),
            scope=_env_optional_str(FINANCE_RECEIPT_V1_SCOPE_ENV),
        )
    if finance_receipt_v1_worker_enabled_from_env():
        log.warning(V1_WORKER_DEPRECATED_MESSAGE)
    if enrichments_enabled_from_env():
        start_periodic_enrichments(
            cfg,
            interval_seconds=_env_float(ENRICHMENT_INTERVAL_ENV, interval_seconds),
            batch_size=_env_int(ENRICHMENT_BATCH_SIZE_ENV, 5),
            lease_seconds=_env_int(ENRICHMENT_LEASE_SECONDS_ENV, 300),
        )
    app = build_app(cfg, port=port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="info", access_log=False)
    uvicorn.Server(config).run()


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        log.warning("invalid %s=%r; using %s", name, value, default)
        return default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        log.warning("invalid %s=%r; using %s", name, value, default)
        return default


def _env_optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        log.warning("invalid %s=%r; ignoring it", name, value)
        return None


def _env_optional_str(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None
