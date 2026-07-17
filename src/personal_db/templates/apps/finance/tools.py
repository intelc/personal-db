"""Declared MCP tool implementations for the finance app.

Registered via app.yaml `mcp_tools` and dispatched by the MCP server's
extension-tool registry. Each function's signature is (cfg, arguments) ->
JSON-serializable, per the declared-tool entrypoint contract (see
core/manifest.py's McpToolSpec docstring). These moved out of the core MCP
server surface (personal_db.services.mcp_server.tools) so services no longer
import personal_db.enrichments.finance directly.
"""

from __future__ import annotations

from typing import Any

from personal_db.config import Config
from personal_db.enrichments.core import get_latest_enrichment
from personal_db.enrichments.finance import (
    RECEIPT_ENRICHMENT,
    RECEIPT_V1_ENRICHMENT,
    enqueue_missing_receipt_enrichments,
    enqueue_missing_receipt_v1_enrichments,
    enrich_transaction_receipt_stub,
    enrich_transaction_receipt_v1,
    run_due_finance_receipt_jobs,
    run_due_finance_receipt_v1_jobs,
)


def finance_enrich_receipt_stub(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    transaction_id = arguments["finance_transaction_id"]
    try:
        return {
            "ok": True,
            **enrich_transaction_receipt_stub(
                cfg,
                transaction_id,
                window_days=arguments.get("window_days", 7),
                scope=arguments.get("scope"),
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "enrichment_name": RECEIPT_ENRICHMENT,
            "input_id": transaction_id,
            "error": str(e),
        }


def finance_enrich_receipt_v1(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    transaction_id = arguments["finance_transaction_id"]
    try:
        return {
            "ok": True,
            **enrich_transaction_receipt_v1(
                cfg,
                transaction_id,
                window_days=arguments.get("window_days", 7),
                scope=arguments.get("scope"),
                max_threads=arguments.get("max_threads", 3),
                max_candidate_threads=arguments.get("max_candidate_threads", 20),
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "enrichment_name": RECEIPT_V1_ENRICHMENT,
            "input_id": transaction_id,
            "error": str(e),
        }


def finance_enqueue_receipt_jobs(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return {
            "ok": True,
            **enqueue_missing_receipt_enrichments(
                cfg,
                limit=arguments.get("limit", 50),
                window_days=arguments.get("window_days", 7),
                scope=arguments.get("scope"),
                force=arguments.get("force", False),
            ),
        }
    except Exception as e:
        return {"ok": False, "enrichment_name": RECEIPT_ENRICHMENT, "error": str(e)}


def finance_enqueue_receipt_v1_jobs(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return {
            "ok": True,
            **enqueue_missing_receipt_v1_enrichments(
                cfg,
                limit=arguments.get("limit", 50),
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                window_days=arguments.get("window_days", 7),
                scope=arguments.get("scope"),
                max_threads=arguments.get("max_threads", 3),
                max_candidate_threads=arguments.get("max_candidate_threads", 20),
                snippet_window_chars=arguments.get("snippet_window_chars", 300),
                only_ready=arguments.get("only_ready", False),
                force=arguments.get("force", False),
            ),
        }
    except Exception as e:
        return {"ok": False, "enrichment_name": RECEIPT_V1_ENRICHMENT, "error": str(e)}


def finance_run_due_receipt_jobs(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"ok": True, **run_due_finance_receipt_jobs(cfg, limit=arguments.get("limit", 5))}
    except Exception as e:
        return {"ok": False, "enrichment_name": RECEIPT_ENRICHMENT, "error": str(e)}


def finance_run_due_receipt_v1_jobs(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"ok": True, **run_due_finance_receipt_v1_jobs(cfg, limit=arguments.get("limit", 5))}
    except Exception as e:
        return {"ok": False, "enrichment_name": RECEIPT_V1_ENRICHMENT, "error": str(e)}


def finance_receipt_latest(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    transaction_id = arguments["finance_transaction_id"]
    enrichment_name = RECEIPT_V1_ENRICHMENT if arguments.get("v1", False) else RECEIPT_ENRICHMENT
    try:
        return {
            "ok": True,
            "latest": get_latest_enrichment(
                cfg,
                enrichment_name,
                "finance_transactions",
                transaction_id,
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "enrichment_name": enrichment_name,
            "input_id": transaction_id,
            "error": str(e),
        }
