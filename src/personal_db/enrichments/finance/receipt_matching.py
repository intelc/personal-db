"""Receipt matching enrichment execution."""

from __future__ import annotations

from typing import Any

from personal_db.core.config import Config
from personal_db.interfaces.email_context import EmailContextProvider
from personal_db.enrichments.agent import (
    EnrichmentAgentHarness,
    EnrichmentAgentRequest,
    EnrichmentAgentResult,
    receipt_harness_from_env,
)
from personal_db.core.enrichment_queue import EnrichmentRunRecord, record_enrichment_run
from personal_db.enrichments.finance.context import require_email_context_provider
from personal_db.enrichments.finance.constants import (
    DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
    RECEIPT_ENRICHMENT,
    RECEIPT_V1_ENRICHMENT,
    RECEIPT_V1_PROMPT_VERSION,
)
from personal_db.enrichments.finance.receipt_signals import (
    _find_amount_combination,
    extract_receipt_evidence_windows,
)
from personal_db.enrichments.finance.transactions import (
    FinanceTransaction,
    _transaction_dict,
    load_transaction,
)

def enrich_transaction_receipt_stub(
    cfg: Config,
    finance_transaction_id: str,
    *,
    window_days: int = 7,
    scope: str | None = None,
    provider: EmailContextProvider | None = None,
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

    context_provider = require_email_context_provider(cfg, provider)
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
    provider: EmailContextProvider | None = None,
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

    context_provider = require_email_context_provider(cfg, provider)
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


def _read_receipt_candidate_contexts(
    context_provider: EmailContextProvider,
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
