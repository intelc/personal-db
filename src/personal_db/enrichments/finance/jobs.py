"""Receipt enrichment queueing and worker orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from personal_db.core.config import Config
from personal_db.context_providers.email import SparkEmailContextProvider
from personal_db.core.db import connect
from personal_db.core.enrichment_queue import (
    apply_enrichment_schema,
    claim_due_enrichment_jobs,
    enqueue_enrichment_job,
    mark_enrichment_job_complete,
    mark_enrichment_job_failed,
    reap_expired_enrichment_jobs,
)
from personal_db.enrichments.agent import EnrichmentAgentHarness
from personal_db.enrichments.finance.constants import (
    DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
    RECEIPT_ENRICHMENT,
    RECEIPT_V1_ENRICHMENT,
)
from personal_db.enrichments.finance.receipt_debug import (
    _readiness_summary,
    _ready_check_payload,
    _summarize_receipt_debug_result,
    debug_transaction_receipt_v1,
)
from personal_db.enrichments.finance.selection import (
    _finance_transaction_columns,
    _receipt_candidate_sql_filters,
)

def enqueue_missing_receipt_enrichments(
    cfg: Config,
    *,
    limit: int = 50,
    window_days: int = 7,
    scope: str | None = None,
    stale_after_days: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Queue receipt enrichment jobs for transactions with missing or stale results."""
    apply_enrichment_schema(cfg)
    rows = _select_enqueue_receipt_rows(
        cfg,
        enrichment_name=RECEIPT_ENRICHMENT,
        limit=limit,
        stale_after_days=stale_after_days,
        force=force,
    )

    enqueued = []
    for transaction_id in rows:
        enqueued.append(
            enqueue_enrichment_job(
                cfg,
                enrichment_name=RECEIPT_ENRICHMENT,
                input_table="finance_transactions",
                input_id=transaction_id,
                priority=100,
                payload={"window_days": int(window_days), "scope": scope},
                force=force,
            )
        )
    return {
        "enrichment_name": RECEIPT_ENRICHMENT,
        "selected": len(rows),
        "ready_selected": len(rows),
        "enqueued": len(enqueued),
        "jobs": enqueued,
    }


def enqueue_missing_receipt_v1_enrichments(
    cfg: Config,
    *,
    limit: int = 50,
    window_days: int = 7,
    scope: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    max_threads: int = 3,
    max_candidate_threads: int = DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    snippet_window_chars: int = DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
    only_ready: bool = False,
    stale_after_days: int | None = None,
    force: bool = False,
    rerun_statuses: Sequence[str] | None = None,
    provider: SparkEmailContextProvider | None = None,
) -> dict[str, Any]:
    return _enqueue_missing_receipt_enrichments(
        cfg,
        enrichment_name=RECEIPT_V1_ENRICHMENT,
        limit=limit,
        window_days=window_days,
        scope=scope,
        start_date=start_date,
        end_date=end_date,
        max_threads=max_threads,
        max_candidate_threads=max_candidate_threads,
        snippet_window_chars=snippet_window_chars,
        only_ready=only_ready,
        stale_after_days=stale_after_days,
        force=force,
        rerun_statuses=rerun_statuses,
        provider=provider,
    )


def run_due_finance_receipt_jobs(
    cfg: Config,
    *,
    limit: int = 5,
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Run due finance receipt jobs synchronously.

    This is intentionally a small worker primitive. The daemon can call it later;
    for now CLI/MCP can invoke it manually.
    """
    reaped = reap_expired_enrichment_jobs(cfg)
    due = claim_due_enrichment_jobs(
        cfg,
        enrichment_name=RECEIPT_ENRICHMENT,
        limit=limit,
        lease_seconds=lease_seconds,
    )
    results = []
    for running in due:
        try:
            payload = running.payload
            from personal_db.enrichments import finance as finance_api

            result = finance_api.enrich_transaction_receipt_stub(
                cfg,
                running.input_id,
                window_days=int(payload.get("window_days") or 7),
                scope=payload.get("scope"),
            )
            mark_enrichment_job_complete(cfg, running.job_id, run_id=result["run_id"])
            results.append({"job": running.as_dict(), "result": result, "ok": True})
        except Exception as e:
            failed = mark_enrichment_job_failed(cfg, running.job_id, error=str(e))
            results.append({"job": running.as_dict(), "failure": failed, "ok": False, "error": str(e)})
    return {
        "enrichment_name": RECEIPT_ENRICHMENT,
        "ran": len(results),
        "reaped": reaped,
        "results": results,
    }


def run_due_finance_receipt_v1_jobs(
    cfg: Config,
    *,
    limit: int = 5,
    lease_seconds: int = 300,
    harness: EnrichmentAgentHarness | None = None,
) -> dict[str, Any]:
    reaped = reap_expired_enrichment_jobs(cfg)
    due = claim_due_enrichment_jobs(
        cfg,
        enrichment_name=RECEIPT_V1_ENRICHMENT,
        limit=limit,
        lease_seconds=lease_seconds,
    )
    results = []
    for running in due:
        try:
            payload = running.payload
            from personal_db.enrichments import finance as finance_api

            result = finance_api.enrich_transaction_receipt_v1(
                cfg,
                running.input_id,
                window_days=int(payload.get("window_days") or 7),
                scope=payload.get("scope"),
                max_threads=int(payload.get("max_threads") or 3),
                max_candidate_threads=int(
                    payload.get("max_candidate_threads")
                    or DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS
                ),
                snippet_window_chars=int(
                    payload.get("snippet_window_chars")
                    or DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS
                ),
                harness=harness,
            )
            mark_enrichment_job_complete(cfg, running.job_id, run_id=result["run_id"])
            results.append({"job": running.as_dict(), "result": result, "ok": True})
        except Exception as e:
            failed = mark_enrichment_job_failed(cfg, running.job_id, error=str(e))
            results.append({"job": running.as_dict(), "failure": failed, "ok": False, "error": str(e)})
    return {
        "enrichment_name": RECEIPT_V1_ENRICHMENT,
        "ran": len(results),
        "reaped": reaped,
        "results": results,
    }


def _enqueue_missing_receipt_enrichments(
    cfg: Config,
    *,
    enrichment_name: str,
    limit: int,
    window_days: int,
    scope: str | None,
    start_date: str | None = None,
    end_date: str | None = None,
    max_threads: int | None = None,
    max_candidate_threads: int | None = None,
    snippet_window_chars: int = DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
    only_ready: bool = False,
    stale_after_days: int | None,
    force: bool,
    rerun_statuses: Sequence[str] | None = None,
    provider: SparkEmailContextProvider | None = None,
) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    rows = _select_enqueue_receipt_rows(
        cfg,
        enrichment_name=enrichment_name,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        stale_after_days=stale_after_days,
        force=force,
        rerun_statuses=rerun_statuses,
    )

    payload: dict[str, Any] = {"window_days": int(window_days), "scope": scope}
    if max_threads is not None:
        payload["max_threads"] = int(max_threads)
    if max_candidate_threads is not None:
        payload["max_candidate_threads"] = int(max_candidate_threads)
    payload["snippet_window_chars"] = int(snippet_window_chars)
    ready_buckets = {"evidence_found", "combined_amount_evidence"}
    readiness_items = []
    skipped = []
    enqueued = []
    for transaction_id in rows:
        item = None
        if only_ready:
            if enrichment_name != RECEIPT_V1_ENRICHMENT:
                raise ValueError("only_ready is supported only for v1 receipt enrichments")
            debug = debug_transaction_receipt_v1(
                cfg,
                transaction_id,
                window_days=window_days,
                scope=scope,
                max_threads=max_threads or 3,
                max_candidate_threads=max_candidate_threads
                or DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
                snippet_window_chars=snippet_window_chars,
                provider=provider,
            )
            item = _summarize_receipt_debug_result(debug)
            readiness_items.append(item)
            if item["failure_bucket"] not in ready_buckets:
                skipped.append(item)
                continue
        job_payload = dict(payload)
        if item is not None:
            job_payload["ready_check"] = _ready_check_payload(item)
        enqueued.append(
            enqueue_enrichment_job(
                cfg,
                enrichment_name=enrichment_name,
                input_table="finance_transactions",
                input_id=transaction_id,
                priority=100,
                payload=job_payload,
                force=force,
            )
        )
    return {
        "enrichment_name": enrichment_name,
        "selected": len(rows),
        "ready_selected": len(enqueued),
        "enqueued": len(enqueued),
        "only_ready": only_ready,
        "rerun_statuses": _normalized_rerun_statuses(rerun_statuses),
        "readiness_summary": _readiness_summary(readiness_items) if only_ready else None,
        "skipped": skipped,
        "jobs": enqueued,
    }


def _select_enqueue_receipt_rows(
    cfg: Config,
    *,
    enrichment_name: str,
    limit: int,
    start_date: str | None = None,
    end_date: str | None = None,
    stale_after_days: int | None = None,
    force: bool = False,
    rerun_statuses: Sequence[str] | None = None,
) -> list[str]:
    stale_cutoff = None
    if stale_after_days is not None and stale_after_days > 0:
        stale_cutoff = (datetime.now(UTC) - timedelta(days=stale_after_days)).isoformat()
    statuses = _normalized_rerun_statuses(rerun_statuses)
    con = connect(cfg.db_path, read_only=True)
    try:
        columns = _finance_transaction_columns(con)
        filters = _receipt_candidate_sql_filters(columns, alias="tx")
        latest_filters = [
            "?",
            "latest.run_id IS NULL",
            "(? IS NOT NULL AND latest.updated_at < ?)",
        ]
        params: list[Any] = [enrichment_name, 1 if force else 0, stale_cutoff, stale_cutoff]
        if statuses:
            latest_filters.append(
                f"latest.status IN ({', '.join('?' for _status in statuses)})"
            )
            params.extend(statuses)
        filters.append(f"({' OR '.join(latest_filters)})")
        if start_date:
            filters.append("tx.date >= ?")
            params.append(start_date)
        if end_date:
            filters.append("tx.date < ?")
            params.append(end_date)
        params.append(int(limit))
        rows = con.execute(
            f"""
            SELECT tx.finance_transaction_id
            FROM finance_transactions tx
            LEFT JOIN enrichment_latest latest
              ON latest.enrichment_name=?
             AND latest.input_table='finance_transactions'
             AND latest.input_id=tx.finance_transaction_id
            WHERE {" AND ".join(filters)}
            ORDER BY tx.date DESC, tx.finance_transaction_id
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


def _normalized_rerun_statuses(statuses: Sequence[str] | None) -> list[str]:
    if not statuses:
        return []
    normalized = []
    for status in statuses:
        value = str(status or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized
