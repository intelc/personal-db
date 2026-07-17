"""Declared background-job entrypoints for the finance app.

Registered via app.yaml `background_jobs` and invoked by the daemon's
generic background-job scheduler (services/daemon/server.py) with the
single-argument signature `(cfg) -> JSON-serializable | None` (see
core/manifest.py's BackgroundJobSpec docstring). Thin wrappers around
personal_db.enrichments.finance; the numeric defaults mirror the former
env-var-gated daemon loops (PERSONAL_DB_FINANCE_RECEIPT_V1_*) that this
registry replaces.

Only the v1 (agent-shaped) receipt jobs are declared here as scheduled
background jobs. The v0 stub jobs (finance.transaction_receipt_stub) remain
reachable as on-demand MCP tools/CLI commands (see tools.py and
cli/enrich_cmd.py) but were never actually wired into the daemon's run()
loop in production: the old start_periodic_enrichments/
start_periodic_finance_receipt_enqueue loops were gated behind
PERSONAL_DB_ENRICHMENTS_ENABLED / PERSONAL_DB_FINANCE_RECEIPT_ENQUEUE_ENABLED,
both default-off, and the v1 worker loop (start_periodic_finance_receipt_v1_
worker) was implemented and unit-tested but never called from run() at all.
Collapsing background_jobs to v1-only therefore does not remove any behavior
that actually ran by default.
"""

from __future__ import annotations

from typing import Any

from personal_db.config import Config
from personal_db.enrichments.finance import (
    enqueue_missing_receipt_v1_enrichments,
    run_due_finance_receipt_v1_jobs,
)


def enqueue_receipt_v1_jobs(cfg: Config) -> dict[str, Any]:
    """Queue missing/stale v1 receipt enrichment jobs. Cadence: every 1h."""
    return enqueue_missing_receipt_v1_enrichments(
        cfg,
        limit=20,
        window_days=7,
        max_threads=3,
        max_candidate_threads=20,
    )


def run_due_receipt_v1_jobs(cfg: Config) -> dict[str, Any]:
    """Run due v1 receipt enrichment jobs. Cadence: every 10m."""
    return run_due_finance_receipt_v1_jobs(cfg, limit=2, lease_seconds=600)
