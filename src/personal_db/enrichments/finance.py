from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from personal_db.config import Config
from personal_db.context_providers.email import SparkEmailContextProvider
from personal_db.db import connect
from personal_db.enrichments.agent import (
    EnrichmentAgentHarness,
    EnrichmentAgentRequest,
    EnrichmentAgentResult,
    receipt_harness_from_env,
)
from personal_db.enrichments.core import (
    EnrichmentRunRecord,
    apply_enrichment_schema,
    claim_due_enrichment_jobs,
    enqueue_enrichment_job,
    mark_enrichment_job_complete,
    mark_enrichment_job_failed,
    reap_expired_enrichment_jobs,
    record_enrichment_run,
)

RECEIPT_ENRICHMENT = "finance.transaction_receipt_stub"
RECEIPT_V1_ENRICHMENT = "finance.transaction_receipt_v1"
RECEIPT_V1_PROMPT_VERSION = "finance-receipt-v1"
DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS = 20
DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS = 300
_GENERIC_MERCHANT_TOKENS = {
    "card",
    "check",
    "corp",
    "debit",
    "inc",
    "llc",
    "online",
    "payment",
    "purchase",
    "sale",
    "service",
    "fee",
    "transaction",
    "yiheng",
    "chen",
}


@dataclass(frozen=True)
class FinanceTransaction:
    finance_transaction_id: str
    date: str | None
    name: str | None
    merchant_name: str | None
    amount: float | None
    category: str | None

    @property
    def merchant_hint(self) -> str | None:
        return self.merchant_name or self.name


def load_transaction(cfg: Config, finance_transaction_id: str) -> FinanceTransaction:
    con = connect(cfg.db_path, read_only=True)
    try:
        row = con.execute(
            """
            SELECT finance_transaction_id, date, name, merchant_name, amount, category
            FROM finance_transactions
            WHERE finance_transaction_id=?
            """,
            (finance_transaction_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError(f"no finance transaction found: {finance_transaction_id}")
    return FinanceTransaction(
        finance_transaction_id=row[0],
        date=row[1],
        name=row[2],
        merchant_name=row[3],
        amount=row[4],
        category=row[5],
    )


def enrich_transaction_receipt_stub(
    cfg: Config,
    finance_transaction_id: str,
    *,
    window_days: int = 7,
    scope: str | None = None,
    provider: SparkEmailContextProvider | None = None,
) -> dict[str, Any]:
    """Find receipt email candidates and persist a stub enrichment result.

    This intentionally does not call an LLM. It records enough structured
    context and evidence for the future agentic enrichment step to consume.
    """
    tx = load_transaction(cfg, finance_transaction_id)
    if tx.amount is None or not tx.date:
        result = {
            "decision": "skipped",
            "reason": "transaction is missing amount or date",
            "transaction": _transaction_dict(tx),
            "receipt_candidate_count": 0,
            "receipt_message_ids": [],
        }
        return record_enrichment_run(
            cfg,
            EnrichmentRunRecord(
                enrichment_name=RECEIPT_ENRICHMENT,
                input_table="finance_transactions",
                input_id=finance_transaction_id,
                status="skipped",
                result=result,
                result_summary=result["reason"],
                confidence=0.0,
            ),
        )

    context_provider = provider or SparkEmailContextProvider.from_config(cfg)
    context = context_provider.search_receipts(
        merchant=tx.merchant_hint,
        amount=abs(tx.amount),
        date_=tx.date,
        window_days=window_days,
        scope=scope,
    )
    message_ids = list(context.data.get("email_ids") or [])
    status = "context_found" if message_ids else "no_context"
    summary = (
        f"Found {len(message_ids)} receipt candidate email(s)"
        if message_ids
        else "No receipt candidate emails found"
    )
    result = {
        "decision": "needs_llm" if message_ids else "no_context",
        "reason": summary,
        "transaction": _transaction_dict(tx),
        "context_query": context.query,
        "receipt_candidate_count": len(message_ids),
        "receipt_message_ids": message_ids,
        "llm_status": "stubbed",
    }
    return record_enrichment_run(
        cfg,
        EnrichmentRunRecord(
            enrichment_name=RECEIPT_ENRICHMENT,
            input_table="finance_transactions",
            input_id=finance_transaction_id,
            status=status,
            result=result,
            evidence=context.evidence,
            result_summary=summary,
            confidence=0.25 if message_ids else 0.0,
            model=None,
            prompt_version="stub-v1",
        ),
    )


def enrich_transaction_receipt_v1(
    cfg: Config,
    finance_transaction_id: str,
    *,
    window_days: int = 7,
    scope: str | None = None,
    max_threads: int = 3,
    max_candidate_threads: int = DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    snippet_window_chars: int = DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
    max_thread_chars: int = 4000,
    provider: SparkEmailContextProvider | None = None,
    harness: EnrichmentAgentHarness | None = None,
) -> dict[str, Any]:
    """Run the agent-shaped receipt enrichment for one finance transaction.

    This path is LLM-ready but provider-neutral. Tests can pass a fake harness;
    production can later provide an OpenAI-backed harness without changing the
    queue or provenance model.
    """
    tx = load_transaction(cfg, finance_transaction_id)
    if tx.amount is None or not tx.date:
        result = {
            "decision": "skipped",
            "reason": "transaction is missing amount or date",
            "transaction": _transaction_dict(tx),
            "receipt_candidate_count": 0,
            "receipt_message_ids": [],
        }
        return record_enrichment_run(
            cfg,
            EnrichmentRunRecord(
                enrichment_name=RECEIPT_V1_ENRICHMENT,
                input_table="finance_transactions",
                input_id=finance_transaction_id,
                status="skipped",
                result=result,
                result_summary=result["reason"],
                confidence=0.0,
                prompt_version=RECEIPT_V1_PROMPT_VERSION,
            ),
        )

    context_provider = provider or SparkEmailContextProvider.from_config(cfg)
    search_context = context_provider.search_receipts(
        merchant=tx.merchant_hint,
        amount=abs(tx.amount),
        date_=tx.date,
        window_days=window_days,
        scope=scope,
    )
    message_ids = list(search_context.data.get("email_ids") or [])
    if not message_ids:
        result = {
            "decision": "no_context",
            "reason": "No receipt candidate emails found",
            "transaction": _transaction_dict(tx),
            "context_query": search_context.query,
            "receipt_candidate_count": 0,
            "receipt_message_ids": [],
        }
        return record_enrichment_run(
            cfg,
            EnrichmentRunRecord(
                enrichment_name=RECEIPT_V1_ENRICHMENT,
                input_table="finance_transactions",
                input_id=finance_transaction_id,
                status="no_context",
                result=result,
                evidence=search_context.evidence,
                result_summary=result["reason"],
                confidence=0.0,
                prompt_version=RECEIPT_V1_PROMPT_VERSION,
            ),
        )

    evidence = list(search_context.evidence)
    candidate_contexts = _read_receipt_candidate_contexts(
        context_provider,
        message_ids,
        max_threads=max_threads,
        max_candidate_threads=max_candidate_threads,
    )
    for _message_id, thread in candidate_contexts:
        evidence.extend(thread.evidence)

    candidate_evidence = [
        extract_receipt_evidence_windows(
            tx,
            message_id,
            thread.raw_text,
            window_chars=snippet_window_chars,
        )
        for message_id, thread in candidate_contexts
    ]
    full_thread_context = _full_thread_context(
        candidate_contexts,
        max_threads=max_threads,
        max_thread_chars=max_thread_chars,
    )
    amount_combination = _find_amount_combination(_transaction_dict(tx), candidate_evidence)
    agent = harness or receipt_harness_from_env()
    request = _receipt_agent_request(
        tx,
        candidate_evidence,
        full_thread_context,
        amount_combination=amount_combination,
    )
    agent_result = _normalize_receipt_agent_result(agent.run(request), request)
    result = {
        "decision": _receipt_decision(agent_result.result),
        "transaction": _transaction_dict(tx),
        "context_query": search_context.query,
        "receipt_candidate_count": len(message_ids),
        "receipt_message_ids": message_ids,
        "inspected_message_ids": [message_id for message_id, _thread in candidate_contexts],
        "full_context_message_ids": [item["message_id"] for item in full_thread_context],
        "candidate_evidence_count": len(candidate_evidence),
        "candidate_evidence": candidate_evidence,
        "amount_combination": amount_combination,
        "agent_result": agent_result.result,
        "agent_raw_text": agent_result.raw_text,
    }
    summary = agent_result.result_summary or _receipt_summary(agent_result.result)
    return record_enrichment_run(
        cfg,
        EnrichmentRunRecord(
            enrichment_name=RECEIPT_V1_ENRICHMENT,
            input_table="finance_transactions",
            input_id=finance_transaction_id,
            status=_receipt_status(agent_result.result),
            result=result,
            evidence=evidence,
            result_summary=summary,
            confidence=agent_result.confidence,
            model=agent_result.model,
            prompt_version=agent_result.prompt_version or request.prompt_version,
        ),
    )


def debug_transaction_receipt_v1(
    cfg: Config,
    finance_transaction_id: str,
    *,
    window_days: int = 7,
    scope: str | None = None,
    max_threads: int = 3,
    max_candidate_threads: int = DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    snippet_window_chars: int = DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
    max_thread_chars: int = 4000,
    run_agent: bool = False,
    provider: SparkEmailContextProvider | None = None,
    harness: EnrichmentAgentHarness | None = None,
) -> dict[str, Any]:
    """Return a non-persisting receipt enrichment debug payload."""
    tx = load_transaction(cfg, finance_transaction_id)
    result: dict[str, Any] = {
        "transaction": _transaction_dict(tx),
        "persisted": False,
        "run_agent": bool(run_agent),
    }
    if tx.amount is None or not tx.date:
        return {
            **result,
            "decision": "skipped",
            "reason": "transaction is missing amount or date",
            "context_query": None,
            "receipt_candidate_count": 0,
            "receipt_message_ids": [],
            "inspected_message_ids": [],
            "full_context_message_ids": [],
            "candidate_evidence": [],
        }

    context_provider = provider or SparkEmailContextProvider.from_config(cfg)
    search_context = context_provider.search_receipts(
        merchant=tx.merchant_hint,
        amount=abs(tx.amount),
        date_=tx.date,
        window_days=window_days,
        scope=scope,
    )
    message_ids = list(search_context.data.get("email_ids") or [])
    candidate_contexts = _read_receipt_candidate_contexts(
        context_provider,
        message_ids,
        max_threads=max_threads,
        max_candidate_threads=max_candidate_threads,
    )
    candidate_evidence = [
        extract_receipt_evidence_windows(
            tx,
            message_id,
            thread.raw_text,
            window_chars=snippet_window_chars,
        )
        for message_id, thread in candidate_contexts
    ]
    full_context = _full_thread_context(
        candidate_contexts,
        max_threads=max_threads,
        max_thread_chars=max_thread_chars,
    )
    amount_combination = _find_amount_combination(_transaction_dict(tx), candidate_evidence)
    debug = {
        **result,
        "decision": "debug",
        "context_query": search_context.query,
        "receipt_candidate_count": len(message_ids),
        "receipt_message_ids": message_ids,
        "inspected_message_ids": [message_id for message_id, _thread in candidate_contexts],
        "full_context_message_ids": [item["message_id"] for item in full_context],
        "candidate_evidence_count": len(candidate_evidence),
        "candidate_evidence": candidate_evidence,
        "amount_combination": amount_combination,
        "full_context": full_context,
        "search_raw_text": search_context.raw_text,
    }
    if not message_ids:
        return {**debug, "decision": "no_context", "reason": "No receipt candidate emails found"}
    if not run_agent:
        return debug

    request = _receipt_agent_request(
        tx,
        candidate_evidence,
        full_context,
        amount_combination=amount_combination,
    )
    agent = harness or receipt_harness_from_env()
    agent_result = _normalize_receipt_agent_result(agent.run(request), request)
    return {
        **debug,
        "decision": _receipt_decision(agent_result.result),
        "agent_result": agent_result.result,
        "agent_raw_text": agent_result.raw_text,
        "agent_summary": agent_result.result_summary,
        "confidence": agent_result.confidence,
        "model": agent_result.model,
        "prompt_version": agent_result.prompt_version or request.prompt_version,
    }


def debug_receipt_batch_v1(
    cfg: Config,
    *,
    limit: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    window_days: int = 7,
    scope: str | None = None,
    max_threads: int = 2,
    max_candidate_threads: int = DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    snippet_window_chars: int = DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
    run_agent: bool = False,
    include_details: bool = False,
    provider: SparkEmailContextProvider | None = None,
    harness: EnrichmentAgentHarness | None = None,
) -> dict[str, Any]:
    """Run non-persisting receipt debug over a recent transaction sample."""
    transaction_ids = _select_receipt_debug_transaction_ids(
        cfg,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
    )
    items = []
    buckets: dict[str, int] = {}
    for transaction_id in transaction_ids:
        debug = debug_transaction_receipt_v1(
            cfg,
            transaction_id,
            window_days=window_days,
            scope=scope,
            max_threads=max_threads,
            max_candidate_threads=max_candidate_threads,
            snippet_window_chars=snippet_window_chars,
            run_agent=run_agent,
            provider=provider,
            harness=harness,
        )
        item = _summarize_receipt_debug_result(debug)
        if include_details:
            item["debug"] = debug
        items.append(item)
        bucket = item["failure_bucket"]
        buckets[bucket] = buckets.get(bucket, 0) + 1

    return {
        "selected": len(transaction_ids),
        "run_agent": bool(run_agent),
        "start_date": start_date,
        "end_date": end_date,
        "window_days": int(window_days),
        "max_threads": int(max_threads),
        "max_candidate_threads": int(max_candidate_threads),
        "summary": {
            "buckets": buckets,
            "ok": buckets.get("ok", 0),
            "ready_for_agent": (
                buckets.get("evidence_found", 0)
                + buckets.get("combined_amount_evidence", 0)
            ),
            "needs_attention": (
                len(transaction_ids)
                - buckets.get("ok", 0)
                - buckets.get("evidence_found", 0)
                - buckets.get("combined_amount_evidence", 0)
            ),
        },
        "items": items,
    }


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
            result = enrich_transaction_receipt_stub(
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
            result = enrich_transaction_receipt_v1(
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


def _finance_transaction_columns(con: Any) -> set[str]:
    return {str(row[1]) for row in con.execute("PRAGMA table_info(finance_transactions)").fetchall()}


def _receipt_candidate_sql_filters(columns: set[str], *, alias: str | None = None) -> list[str]:
    prefix = f"{alias}." if alias else ""
    filters = [
        f"{prefix}date IS NOT NULL",
        f"{prefix}amount IS NOT NULL",
        f"{prefix}amount > 0",
    ]
    if "pending" in columns:
        filters.append(f"COALESCE({prefix}pending, 0) = 0")
    if "is_credit_card_payment" in columns:
        filters.append(f"COALESCE({prefix}is_credit_card_payment, 0) = 0")
    if "is_internal_transfer" in columns:
        filters.append(f"COALESCE({prefix}is_internal_transfer, 0) = 0")
    return filters


def _normalized_rerun_statuses(statuses: Sequence[str] | None) -> list[str]:
    if not statuses:
        return []
    normalized = []
    for status in statuses:
        value = str(status or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _ready_check_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "failure_bucket": item["failure_bucket"],
        "best_candidate_ids": item["best_candidate_ids"],
        "has_complete_evidence": item["has_complete_evidence"],
        "amount_combination": item.get("amount_combination"),
    }


def _readiness_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, int] = {}
    for item in items:
        bucket = item["failure_bucket"]
        buckets[bucket] = buckets.get(bucket, 0) + 1
    return {
        "screened": len(items),
        "buckets": buckets,
        "ready": buckets.get("evidence_found", 0) + buckets.get("combined_amount_evidence", 0),
        "not_ready": len(items) - buckets.get("evidence_found", 0) - buckets.get("combined_amount_evidence", 0),
    }


def _select_receipt_debug_transaction_ids(
    cfg: Config,
    *,
    limit: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    params: list[Any] = []
    con = connect(cfg.db_path, read_only=True)
    try:
        columns = _finance_transaction_columns(con)
        filters = _receipt_candidate_sql_filters(columns)
        if start_date:
            filters.append("date >= ?")
            params.append(start_date)
        if end_date:
            filters.append("date < ?")
            params.append(end_date)
        params.append(max(0, int(limit)))
        rows = con.execute(
            f"""
            SELECT finance_transaction_id
            FROM finance_transactions
            WHERE {" AND ".join(filters)}
            ORDER BY date DESC, finance_transaction_id
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


def _summarize_receipt_debug_result(debug: dict[str, Any]) -> dict[str, Any]:
    candidates = list(debug.get("candidate_evidence") or [])
    ranked = sorted(
        (
            {
                "message_id": c.get("message_id"),
                "score": _candidate_signal_score(c),
                "signals": c.get("signals") or {},
                "snippet_count": c.get("snippet_count") or 0,
            }
            for c in candidates
        ),
        key=lambda c: c["score"],
        reverse=True,
    )
    best_candidates = [c for c in ranked if c["score"] > 0][:5]
    has_complete_evidence = any(_candidate_has_complete_receipt_signals(c) for c in candidates)
    amount_combination = _find_amount_combination(debug.get("transaction") or {}, candidates)
    aggregate_signals = {
        signal: any((c.get("signals") or {}).get(signal) for c in candidates)
        for signal in ("amount", "date", "merchant", "receipt_language")
    }
    agent_result = debug.get("agent_result") or {}
    failure_bucket = _receipt_debug_failure_bucket(
        debug,
        aggregate_signals=aggregate_signals,
        has_complete_evidence=has_complete_evidence,
        amount_combination=amount_combination,
    )
    return {
        "transaction": debug.get("transaction"),
        "receipt_candidate_count": debug.get("receipt_candidate_count", 0),
        "inspected_count": len(debug.get("inspected_message_ids") or []),
        "best_candidate_ids": [c["message_id"] for c in best_candidates],
        "best_candidates": best_candidates,
        "aggregate_signals": aggregate_signals,
        "has_complete_evidence": has_complete_evidence,
        "amount_combination": amount_combination,
        "agent_decision": agent_result.get("receipt_match"),
        "agent_confidence": debug.get("confidence"),
        "failure_bucket": failure_bucket,
        "reason": _receipt_debug_bucket_reason(
            failure_bucket,
            debug,
            aggregate_signals=aggregate_signals,
        ),
    }


def _candidate_signal_score(candidate: dict[str, Any]) -> int:
    signals = candidate.get("signals") or {}
    return (
        (4 if signals.get("amount") else 0)
        + (3 if signals.get("date") else 0)
        + (2 if signals.get("merchant") else 0)
        + (1 if signals.get("receipt_language") else 0)
    )


def _candidate_has_complete_receipt_signals(candidate: dict[str, Any]) -> bool:
    signals = candidate.get("signals") or {}
    return bool(signals.get("amount") and signals.get("date") and signals.get("merchant"))


def _find_amount_combination(
    transaction: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    max_items: int = 5,
) -> dict[str, Any] | None:
    target = _decimal_amount(transaction.get("amount"))
    if target is None:
        return None
    eligible = []
    for candidate in candidates:
        signals = candidate.get("signals") or {}
        if not (signals.get("date") and signals.get("merchant")):
            continue
        primary = candidate.get("primary_amount") or {}
        value = _decimal_amount(primary.get("value") if isinstance(primary, dict) else None)
        if value is None or value <= 0 or value >= target:
            continue
        eligible.append(
            {
                "message_id": candidate.get("message_id"),
                "value": value,
                "matched": primary.get("matched") if isinstance(primary, dict) else None,
                "snippet": primary.get("snippet") if isinstance(primary, dict) else None,
            }
        )
    # Keep the search small and deterministic: closest/largest charges first tends
    # to find ride-share daily batches without exploring noisy marketing amounts.
    eligible = sorted(eligible, key=lambda item: item["value"], reverse=True)[:12]
    combo = _find_decimal_subset(eligible, target, max_items=max_items)
    if not combo:
        return None
    total = sum((item["value"] for item in combo), Decimal("0.00"))
    return {
        "target": f"{target:.2f}",
        "total": f"{total:.2f}",
        "message_ids": [str(item["message_id"]) for item in combo],
        "components": [
            {
                "message_id": str(item["message_id"]),
                "value": f"{item['value']:.2f}",
                "matched": item.get("matched"),
                "snippet": item.get("snippet"),
            }
            for item in combo
        ],
    }


def _find_decimal_subset(
    items: list[dict[str, Any]],
    target: Decimal,
    *,
    max_items: int,
) -> list[dict[str, Any]] | None:
    cents_target = int((target * 100).to_integral_value())
    cents = [int((item["value"] * 100).to_integral_value()) for item in items]

    def search(start: int, remaining: int, chosen: list[int]) -> list[int] | None:
        if remaining == 0 and len(chosen) >= 2:
            return chosen
        if remaining <= 0 or len(chosen) >= max_items:
            return None
        for i in range(start, len(items)):
            found = search(i + 1, remaining - cents[i], [*chosen, i])
            if found is not None:
                return found
        return None

    indexes = search(0, cents_target, [])
    if indexes is None:
        return None
    return [items[i] for i in indexes]


def _receipt_debug_failure_bucket(
    debug: dict[str, Any],
    *,
    aggregate_signals: dict[str, bool],
    has_complete_evidence: bool,
    amount_combination: dict[str, Any] | None,
) -> str:
    if debug.get("decision") == "skipped":
        return "skipped"
    if not debug.get("receipt_candidate_count"):
        return "no_candidates"
    if not debug.get("inspected_message_ids"):
        return "no_inspected_candidates"
    agent_result = debug.get("agent_result") or {}
    agent_match = str(agent_result.get("receipt_match") or "").lower()
    if has_complete_evidence:
        if agent_match == "yes":
            return "ok"
        if agent_match == "unknown":
            return "agent_uncertain"
        if agent_match == "no":
            return "agent_mismatch"
        return "evidence_found"
    if amount_combination:
        if agent_match == "yes":
            return "ok"
        if agent_match == "unknown":
            return "agent_uncertain"
        if agent_match == "no":
            return "agent_mismatch"
        return "combined_amount_evidence"
    if not aggregate_signals.get("amount"):
        return "no_exact_amount"
    if not aggregate_signals.get("date"):
        return "no_date_match"
    if not aggregate_signals.get("merchant"):
        return "no_merchant_match"
    return "partial_evidence"


def _receipt_debug_bucket_reason(
    bucket: str,
    debug: dict[str, Any],
    *,
    aggregate_signals: dict[str, bool],
) -> str:
    if bucket == "ok":
        return "agent matched a candidate with amount/date/merchant evidence"
    if bucket == "evidence_found":
        return "found candidate evidence; agent was not run"
    if bucket == "combined_amount_evidence":
        return "candidate receipt amounts combine to the transaction amount; agent was not run"
    if bucket == "no_candidates":
        return "Spark returned no receipt candidates"
    if bucket == "no_exact_amount":
        return "inspected candidates did not contain the transaction amount"
    if bucket == "no_date_match":
        return "inspected candidates did not contain the transaction date"
    if bucket == "no_merchant_match":
        return "inspected candidates did not contain the merchant hint"
    if bucket == "partial_evidence":
        return f"signals found across candidates but not together: {aggregate_signals}"
    if bucket == "agent_uncertain":
        return "candidate evidence exists but the agent returned unknown"
    if bucket == "agent_mismatch":
        return "candidate evidence exists but the agent returned no"
    if bucket == "skipped":
        return str(debug.get("reason") or "transaction is missing required fields")
    return bucket.replace("_", " ")


def _extract_currency_amounts(text: str, *, window_chars: int) -> list[dict[str, Any]]:
    values = []
    seen: set[tuple[str, str]] = set()
    money_re = re.compile(
        r"(?<![A-Za-z0-9])(?:(?:USD\s*)?\$\s*(\d{1,6}(?:,\d{3})*\.\d{2})|USD\s+(\d{1,6}(?:,\d{3})*\.\d{2}))(?![A-Za-z0-9])",
        flags=re.IGNORECASE,
    )
    for match in money_re.finditer(text):
        matched = match.group(0).strip()
        # Avoid bare decimals buried inside URLs/encoded tracking payloads. Receipt
        # text usually has whitespace or punctuation around the amount.
        context = text[max(0, match.start() - 12) : min(len(text), match.end() + 12)]
        if "%" in context and "$" not in matched:
            continue
        value = _decimal_amount(match.group(1) or match.group(2))
        if value is None:
            continue
        key = (f"{value:.2f}", matched)
        if key in seen:
            continue
        seen.add(key)
        values.append(
            {
                "value": f"{value:.2f}",
                "matched": matched,
                "snippet": _evidence_snippet(
                    text,
                    match.start(),
                    match.end(),
                    window_chars=window_chars,
                ),
            }
        )
        if len(values) >= 24:
            break
    return values


def _decimal_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace("$", "").replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def extract_receipt_evidence_windows(
    tx: FinanceTransaction,
    message_id: str,
    text: str,
    *,
    window_chars: int = DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
) -> dict[str, Any]:
    """Extract deterministic receipt evidence snippets from one email thread."""
    signal_terms = {
        "amount": _amount_terms(tx.amount),
        "date": _date_terms(tx.date),
        "merchant": _merchant_terms(tx.merchant_hint, tx.name),
        "receipt_language": ["receipt", "total", "charged", "payment", "order", "invoice"],
    }
    snippets = []
    matched_terms: dict[str, list[str]] = {}
    seen_snippets: set[tuple[str, str, str]] = set()
    for signal, terms in signal_terms.items():
        for term in terms:
            for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
                snippet = _evidence_snippet(
                    text,
                    match.start(),
                    match.end(),
                    window_chars=window_chars,
                )
                key = (signal, term.lower(), snippet)
                if key in seen_snippets:
                    continue
                seen_snippets.add(key)
                matched_terms.setdefault(signal, [])
                if term not in matched_terms[signal]:
                    matched_terms[signal].append(term)
                snippets.append(
                    {
                        "signal": signal,
                        "matched": text[match.start() : match.end()],
                        "snippet": snippet,
                    }
                )
                if len(snippets) >= 16:
                    break
            if len(snippets) >= 16:
                break
        if len(snippets) >= 16:
            break

    amount_values = _extract_currency_amounts(text, window_chars=window_chars)
    signals = {signal: bool(matched_terms.get(signal)) for signal in signal_terms}
    return {
        "message_id": str(message_id),
        "source_ref": f"spark_email:message:{message_id}",
        "signals": signals,
        "matched_terms": matched_terms,
        "amount_values": amount_values,
        "primary_amount": amount_values[0] if amount_values else None,
        "snippet_count": len(snippets),
        "snippets": snippets,
    }


def _read_receipt_candidate_contexts(
    context_provider: SparkEmailContextProvider,
    message_ids: list[Any],
    *,
    max_threads: int,
    max_candidate_threads: int,
) -> list[tuple[str, Any]]:
    full_thread_limit = max(0, int(max_threads))
    candidate_limit = max(full_thread_limit, max(0, int(max_candidate_threads)))
    return [
        (str(message_id), context_provider.read_thread(str(message_id)))
        for message_id in message_ids[:candidate_limit]
    ]


def _full_thread_context(
    candidate_contexts: list[tuple[str, Any]],
    *,
    max_threads: int,
    max_thread_chars: int,
) -> list[dict[str, Any]]:
    full_thread_limit = max(0, int(max_threads))
    return [
        {
            "kind": "full_thread",
            "message_id": message_id,
            "source_ref": f"spark_email:message:{message_id}",
            "text": _truncate_text(thread.raw_text, max_thread_chars),
        }
        for message_id, thread in candidate_contexts[:full_thread_limit]
    ]


def _receipt_agent_request(
    tx: FinanceTransaction,
    candidate_evidence: list[dict[str, Any]],
    full_thread_context: list[dict[str, Any]],
    *,
    amount_combination: dict[str, Any] | None = None,
) -> EnrichmentAgentRequest:
    context: list[dict[str, Any]] = [
        {
            "kind": "candidate_evidence",
            "description": (
                "Deterministic snippets around amount/date/merchant/receipt "
                "matches for every inspected candidate email thread."
            ),
            "candidates": candidate_evidence,
        }
        if candidate_evidence
        else {
            "kind": "candidate_evidence",
            "description": "No candidate threads were read.",
            "candidates": [],
        },
    ]
    if amount_combination:
        context.append(
            {
                "kind": "amount_combination",
                "description": (
                    "Candidate receipt amounts whose sum matches the transaction amount. "
                    "This can happen when a card processor batches same-day charges."
                ),
                "combination": amount_combination,
            }
        )
    context.extend(full_thread_context)
    return EnrichmentAgentRequest(
        enrichment_name=RECEIPT_V1_ENRICHMENT,
        prompt_version=RECEIPT_V1_PROMPT_VERSION,
        input={
            "transaction": _transaction_dict(tx),
            "task": (
                "Determine whether the email context explains this finance "
                "transaction and produce structured receipt metadata."
            ),
            "expected_result_fields": [
                "receipt_match",
                "merchant",
                "description",
                "category",
                "amount",
                "currency",
                "transaction_date",
                "reasoning",
            ],
        },
        context=context,
    )


def _normalize_receipt_agent_result(
    result: EnrichmentAgentResult,
    request: EnrichmentAgentRequest,
) -> EnrichmentAgentResult:
    raw = dict(result.result or {})
    normalized = {
        "receipt_match": str(raw.get("receipt_match") or "unknown").lower(),
        "merchant": raw.get("merchant"),
        "description": raw.get("description"),
        "category": raw.get("category"),
        "amount": raw.get("amount"),
        "currency": raw.get("currency"),
        "transaction_date": raw.get("transaction_date"),
        "reasoning": raw.get("reasoning") or raw.get("explanation"),
    }
    match = normalized["receipt_match"]
    if match not in {"yes", "no", "unknown"}:
        normalized["receipt_match"] = "unknown"
    confidence = result.confidence
    if confidence is not None:
        confidence = max(0.0, min(1.0, float(confidence)))
    return EnrichmentAgentResult(
        result=normalized,
        result_summary=result.result_summary or _receipt_summary(normalized),
        confidence=confidence,
        model=result.model,
        prompt_version=result.prompt_version or request.prompt_version,
        raw_text=result.raw_text,
    )


def _receipt_decision(result: dict[str, Any]) -> str:
    match = result.get("receipt_match")
    if match == "yes":
        return "receipt_matched"
    if match == "no":
        return "receipt_not_matched"
    return "uncertain"


def _receipt_status(result: dict[str, Any]) -> str:
    match = result.get("receipt_match")
    if match == "yes":
        return "enriched"
    if match == "no":
        return "no_match"
    return "uncertain"


def _receipt_summary(result: dict[str, Any]) -> str:
    match = result.get("receipt_match", "unknown")
    merchant = result.get("merchant") or "unknown merchant"
    return f"Receipt match: {match} ({merchant})"


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return compact[: int(limit)] + "\n[truncated]"


def _amount_terms(amount: float | None) -> list[str]:
    if amount is None:
        return []
    value = abs(float(amount))
    fixed = f"{value:.2f}"
    terms = [fixed, f"${fixed}", f"USD {fixed}", f"US${fixed}"]
    if fixed.endswith("0"):
        terms.append(fixed.rstrip("0").rstrip("."))
    if value >= 1000:
        comma = f"{value:,.2f}"
        terms.extend([comma, f"${comma}", f"USD {comma}"])
    return _unique_nonempty(terms)


def _date_terms(date_value: str | None) -> list[str]:
    if not date_value:
        return []
    terms = [date_value]
    try:
        dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
    except ValueError:
        return _unique_nonempty(terms)
    month = dt.strftime("%B")
    month_abbr = dt.strftime("%b")
    terms.extend(
        [
            dt.strftime("%m/%d/%Y"),
            f"{dt.month}/{dt.day}/{dt.year}",
            f"{month} {dt.day}, {dt.year}",
            f"{month} {dt.day}",
            f"{month_abbr} {dt.day}, {dt.year}",
            f"{month_abbr} {dt.day}",
        ]
    )
    return _unique_nonempty(terms)


def _merchant_terms(merchant: str | None, transaction_name: str | None) -> list[str]:
    values = [merchant] if merchant else [transaction_name]
    terms: list[str] = []
    for value in values:
        if not value:
            continue
        compact = re.sub(r"\s+", " ", value).strip()
        if compact:
            terms.append(compact)
        for token in re.findall(r"[A-Za-z0-9]{3,}", value):
            lowered = token.lower()
            if lowered not in _GENERIC_MERCHANT_TOKENS:
                terms.append(token)
    return _unique_nonempty(terms)


def _evidence_snippet(text: str, start: int, end: int, *, window_chars: int) -> str:
    paragraph_start = text.rfind("\n\n", 0, start)
    paragraph_end = text.find("\n\n", end)
    if paragraph_start == -1:
        paragraph_start = 0
    else:
        paragraph_start += 2
    if paragraph_end == -1:
        paragraph_end = len(text)
    paragraph = text[paragraph_start:paragraph_end].strip()
    max_paragraph_chars = max(200, int(window_chars) * 2)
    if paragraph and len(paragraph) <= max_paragraph_chars:
        return _clean_receipt_snippet(paragraph)

    radius = max(50, int(window_chars))
    snippet_start = max(0, start - radius)
    snippet_end = min(len(text), end + radius)
    prefix = "..." if snippet_start > 0 else ""
    suffix = "..." if snippet_end < len(text) else ""
    return prefix + _clean_receipt_snippet(text[snippet_start:snippet_end]) + suffix


def _clean_receipt_snippet(text: str) -> str:
    without_markdown_urls = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    without_bare_urls = re.sub(r"https?://\S+", "", without_markdown_urls)
    without_encoded_noise = re.sub(
        r"\S*(?:%[0-9A-Fa-f]{2}\S*){3,}",
        "",
        without_bare_urls,
    )
    without_tracking_tail = re.sub(
        r"\S*(?:safelinks|reserved=0|data=)\S*",
        "",
        without_encoded_noise,
        flags=re.IGNORECASE,
    )
    return _compact_whitespace(without_tracking_tail)


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = value.strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            out.append(normalized)
    return out


def _transaction_dict(tx: FinanceTransaction) -> dict[str, Any]:
    return {
        "finance_transaction_id": tx.finance_transaction_id,
        "date": tx.date,
        "name": tx.name,
        "merchant_name": tx.merchant_name,
        "amount": tx.amount,
        "category": tx.category,
    }
