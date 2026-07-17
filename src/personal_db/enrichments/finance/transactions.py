"""Finance transaction loading helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from personal_db.core.config import Config
from personal_db.core.db import connect

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


def _transaction_dict(tx: FinanceTransaction) -> dict[str, Any]:
    return {
        "finance_transaction_id": tx.finance_transaction_id,
        "date": tx.date,
        "name": tx.name,
        "merchant_name": tx.merchant_name,
        "amount": tx.amount,
        "category": tx.category,
    }
