"""Finance transaction selection helpers for receipt workflows."""

from __future__ import annotations

from typing import Any

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
