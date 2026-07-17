"""Finance enrichment constants."""

from __future__ import annotations

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
}
