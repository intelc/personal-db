"""Non-persisting receipt enrichment debug helpers."""

from __future__ import annotations

from typing import Any

from personal_db.core.config import Config
from personal_db.interfaces.email_context import EmailContextProvider
from personal_db.core.db import connect
from personal_db.enrichments.agent import EnrichmentAgentHarness, receipt_harness_from_env
from personal_db.enrichments.finance.constants import (
    DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
)
from personal_db.enrichments.finance.context import require_email_context_provider
from personal_db.enrichments.finance.receipt_matching import (
    _full_thread_context,
    _normalize_receipt_agent_result,
    _read_receipt_candidate_contexts,
    _receipt_agent_request,
    _receipt_decision,
    _truncate_text,
)
from personal_db.enrichments.finance.receipt_signals import (
    _candidate_has_complete_receipt_signals,
    _candidate_signal_score,
    _find_amount_combination,
    extract_receipt_evidence_windows,
)
from personal_db.enrichments.finance.selection import (
    _finance_transaction_columns,
    _receipt_candidate_sql_filters,
)
from personal_db.enrichments.finance.transactions import _transaction_dict, load_transaction

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
    provider: EmailContextProvider | None = None,
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

    context_provider = require_email_context_provider(cfg, provider)
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
    provider: EmailContextProvider | None = None,
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
