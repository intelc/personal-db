from __future__ import annotations

import logging
import threading

from personal_db.config import Config
from personal_db.enrichments.finance import run_due_finance_receipt_v1_jobs

log = logging.getLogger("personal_db.worker.enrich")

FINANCE_RECEIPT_V1 = "finance-receipt-v1"
SUPPORTED_KINDS = {FINANCE_RECEIPT_V1}


def run_once(
    cfg: Config,
    *,
    kind: str = FINANCE_RECEIPT_V1,
    batch_size: int = 1,
    lease_seconds: int = 1200,
) -> dict:
    """Run one worker tick for a supported enrichment kind."""
    if kind != FINANCE_RECEIPT_V1:
        raise ValueError(f"unsupported enrichment worker kind: {kind}")
    return run_due_finance_receipt_v1_jobs(
        cfg,
        limit=batch_size,
        lease_seconds=lease_seconds,
    )


def run_loop(
    cfg: Config,
    *,
    kind: str = FINANCE_RECEIPT_V1,
    batch_size: int = 1,
    interval_seconds: float = 900,
    lease_seconds: int = 1200,
    once: bool = False,
    stop_event: threading.Event | None = None,
) -> None:
    """Run a foreground enrichment worker loop.

    Errors inside one tick are caught and logged so launchd can keep a single
    worker process alive through transient Spark/OpenAI failures.
    """
    stop = stop_event or threading.Event()
    while not stop.is_set():
        try:
            run_once(
                cfg,
                kind=kind,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
            )
        except Exception:
            log.exception("enrichment worker tick failed")
        if once:
            return
        stop.wait(timeout=interval_seconds)
