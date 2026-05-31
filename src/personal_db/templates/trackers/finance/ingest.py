"""Derived finance model over source-owned finance export views."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from personal_db.db import connect
from personal_db.tracker import Tracker

ACCOUNT_GROUPS = {"cash", "credit_card", "investments", "other"}
SELF_OWNERS = {"self", "me", "personal"}

ACCOUNT_EXPORT_COLUMNS = [
    "source",
    "source_account_id",
    "finance_account_id",
    "owner",
    "account_group",
    "institution_name",
    "account_name",
    "mask",
    "type",
    "subtype",
    "current_balance",
    "available_balance",
    "iso_currency_code",
    "include_in_net_worth",
    "parent_draw_source",
    "as_of",
    "raw_json",
]
TRANSACTION_EXPORT_COLUMNS = [
    "source",
    "source_transaction_id",
    "finance_transaction_id",
    "source_account_id",
    "finance_account_id",
    "date",
    "name",
    "merchant_name",
    "amount",
    "source_amount",
    "pending",
    "category",
    "is_credit_card_payment",
    "is_internal_transfer",
    "raw_json",
]
HOLDING_EXPORT_COLUMNS = [
    "source",
    "source_holding_id",
    "finance_holding_id",
    "source_account_id",
    "finance_account_id",
    "security_id",
    "security_name",
    "ticker",
    "type",
    "quantity",
    "cost_basis",
    "price",
    "value",
    "as_of",
    "raw_json",
]
HOLDING_SNAPSHOT_EXPORT_COLUMNS = [
    "source",
    "source_holding_snapshot_id",
    "finance_holding_snapshot_id",
    "source_holding_id",
    "finance_holding_id",
    "source_account_id",
    "finance_account_id",
    "date",
    "security_id",
    "security_name",
    "ticker",
    "type",
    "quantity",
    "cost_basis",
    "price",
    "value",
    "as_of",
    "raw_json",
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _execute(t: Tracker, sql: str, params: tuple[Any, ...] = ()) -> None:
    con = connect(t.cfg.db_path)
    con.execute(sql, params)
    con.commit()
    con.close()


def _read_rows(t: Tracker, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    con = connect(t.cfg.db_path)
    try:
        cur = con.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _coerce_int(value: Any) -> int:
    try:
        return 1 if int(value or 0) else 0
    except (TypeError, ValueError):
        return 0


def _owner_flags(owner: str) -> tuple[int, int]:
    normalized = str(owner or "self").strip().lower()
    if normalized not in SELF_OWNERS:
        return 0, 1
    return 1, 0


def _quote_ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise RuntimeError(f"Unsafe SQLite identifier: {name}")
    return f'"{name}"'


def _export_views(t: Tracker, suffix: str) -> list[str]:
    rows = _read_rows(
        t,
        """
        SELECT name
        FROM sqlite_master
        WHERE type='view'
          AND name LIKE ?
        ORDER BY name
        """,
        (f"%{suffix}",),
    )
    return [row["name"] for row in rows if str(row["name"]).endswith(suffix)]


def _read_export_view(
    t: Tracker,
    view: str,
    columns: list[str],
) -> list[dict[str, Any]]:
    cols = ", ".join(_quote_ident(col) for col in columns)
    try:
        return _read_rows(t, f"SELECT {cols} FROM {_quote_ident(view)}")
    except sqlite3.OperationalError as exc:
        t.log.warning("finance: skipping export view %s: %s", view, exc)
        return []


def _load_exported_accounts(t: Tracker) -> list[dict[str, Any]]:
    rows = []
    for view in _export_views(t, "_finance_accounts_export"):
        rows.extend(_read_export_view(t, view, ACCOUNT_EXPORT_COLUMNS))

    out = []
    for row in rows:
        account_id = str(row.get("finance_account_id") or "").strip()
        source_account_id = str(row.get("source_account_id") or "").strip()
        source = str(row.get("source") or "").strip()
        if not account_id or not source_account_id or not source:
            continue
        owner = str(row.get("owner") or "self").strip() or "self"
        include, parent_draw = _owner_flags(owner)
        group = str(row.get("account_group") or "other").strip()
        if group not in ACCOUNT_GROUPS:
            group = "other"
        out.append(
            {
                "finance_account_id": account_id,
                "source": source,
                "source_account_id": source_account_id,
                "owner": owner,
                "account_group": group,
                "institution_name": row.get("institution_name"),
                "account_name": row.get("account_name"),
                "mask": row.get("mask"),
                "type": row.get("type"),
                "subtype": row.get("subtype"),
                "current_balance": row.get("current_balance"),
                "available_balance": row.get("available_balance"),
                "iso_currency_code": row.get("iso_currency_code"),
                "include_in_net_worth": include,
                "parent_draw_source": parent_draw,
                "as_of": row.get("as_of") or _now_iso(),
                "raw_json": row.get("raw_json"),
            }
        )
    return out


def _account_map(accounts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["finance_account_id"]: row for row in accounts}


def _parent_draw_amount(account: dict[str, Any], amount: float, is_card_payment: bool) -> float:
    if account.get("parent_draw_source") and amount > 0 and not is_card_payment:
        return amount
    return 0.0


def _load_exported_transactions(
    t: Tracker,
    accounts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for view in _export_views(t, "_finance_transactions_export"):
        rows.extend(_read_export_view(t, view, TRANSACTION_EXPORT_COLUMNS))

    out = []
    for row in rows:
        account = accounts.get(str(row.get("finance_account_id") or ""))
        if not account:
            continue
        txn_id = str(row.get("finance_transaction_id") or "").strip()
        source_txn_id = str(row.get("source_transaction_id") or "").strip()
        source = str(row.get("source") or "").strip()
        if not txn_id or not source_txn_id or not source:
            continue
        amount = _coerce_float(row.get("amount"))
        is_card_payment = _coerce_int(row.get("is_credit_card_payment"))
        is_transfer = _coerce_int(row.get("is_internal_transfer"))
        out.append(
            {
                "finance_transaction_id": txn_id,
                "source": source,
                "source_transaction_id": source_txn_id,
                "finance_account_id": account["finance_account_id"],
                "source_account_id": account["source_account_id"],
                "date": row.get("date"),
                "name": row.get("name"),
                "merchant_name": row.get("merchant_name"),
                "amount": amount,
                "source_amount": row.get("source_amount"),
                "pending": _coerce_int(row.get("pending")),
                "category": row.get("category"),
                "owner": account["owner"],
                "account_group": account["account_group"],
                "is_credit_card_payment": is_card_payment,
                "is_internal_transfer": is_transfer,
                "parent_draw": _parent_draw_amount(account, amount, bool(is_card_payment)),
                "raw_json": row.get("raw_json"),
            }
        )
    return out


def _load_exported_holdings(
    t: Tracker,
    accounts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for view in _export_views(t, "_finance_holdings_export"):
        rows.extend(_read_export_view(t, view, HOLDING_EXPORT_COLUMNS))

    out = []
    for row in rows:
        account = accounts.get(str(row.get("finance_account_id") or ""))
        if not account:
            continue
        holding_id = str(row.get("finance_holding_id") or "").strip()
        source_holding_id = str(row.get("source_holding_id") or "").strip()
        source = str(row.get("source") or "").strip()
        if not holding_id or not source_holding_id or not source:
            continue
        out.append(
            {
                "finance_holding_id": holding_id,
                "source": source,
                "source_holding_id": source_holding_id,
                "finance_account_id": account["finance_account_id"],
                "source_account_id": account["source_account_id"],
                "owner": account["owner"],
                "account_group": account["account_group"],
                "institution_name": account.get("institution_name"),
                "account_name": account.get("account_name"),
                "security_id": row.get("security_id"),
                "security_name": row.get("security_name"),
                "ticker": row.get("ticker"),
                "type": row.get("type"),
                "quantity": row.get("quantity"),
                "cost_basis": row.get("cost_basis"),
                "price": row.get("price"),
                "value": row.get("value"),
                "as_of": row.get("as_of"),
                "raw_json": row.get("raw_json"),
            }
        )
    return out


def _holding_snapshot_from_row(
    row: dict[str, Any],
    account: dict[str, Any],
    *,
    now: str,
) -> dict[str, Any] | None:
    snapshot_id = str(row.get("finance_holding_snapshot_id") or "").strip()
    source_snapshot_id = str(row.get("source_holding_snapshot_id") or "").strip()
    holding_id = str(row.get("finance_holding_id") or "").strip()
    source_holding_id = str(row.get("source_holding_id") or "").strip()
    source = str(row.get("source") or "").strip()
    if not source_snapshot_id and source and source_holding_id:
        source_snapshot_id = f"{source_holding_id}:{row.get('date') or row.get('as_of') or now}"
    if not snapshot_id and source and source_snapshot_id:
        snapshot_id = f"{source}:{source_snapshot_id}"
    if not holding_id or not source_holding_id or not source or not snapshot_id or not source_snapshot_id:
        return None
    day = str(row.get("date") or row.get("as_of") or now)[:10]
    return {
        "finance_holding_snapshot_id": snapshot_id,
        "date": day,
        "source": source,
        "source_holding_snapshot_id": source_snapshot_id,
        "finance_holding_id": holding_id,
        "source_holding_id": source_holding_id,
        "finance_account_id": account["finance_account_id"],
        "source_account_id": account["source_account_id"],
        "owner": account["owner"],
        "account_group": account["account_group"],
        "institution_name": account.get("institution_name"),
        "account_name": account.get("account_name"),
        "security_id": row.get("security_id"),
        "security_name": row.get("security_name"),
        "ticker": row.get("ticker"),
        "type": row.get("type"),
        "quantity": row.get("quantity"),
        "cost_basis": row.get("cost_basis"),
        "price": row.get("price"),
        "value": row.get("value"),
        "as_of": row.get("as_of"),
        "raw_json": row.get("raw_json"),
    }


def _load_exported_holding_snapshots(
    t: Tracker,
    accounts: dict[str, dict[str, Any]],
    current_holdings: list[dict[str, Any]],
    now: str,
) -> list[dict[str, Any]]:
    rows = []
    for view in _export_views(t, "_finance_holding_snapshots_export"):
        rows.extend(_read_export_view(t, view, HOLDING_SNAPSHOT_EXPORT_COLUMNS))

    out = []
    for row in rows:
        account = accounts.get(str(row.get("finance_account_id") or ""))
        if not account:
            continue
        snapshot = _holding_snapshot_from_row(row, account, now=now)
        if snapshot:
            out.append(snapshot)

    sources_with_snapshot_exports = {str(row.get("source") or "").strip() for row in rows}
    for holding in current_holdings:
        if holding["source"] in sources_with_snapshot_exports:
            continue
        snapshot_row = {
            **holding,
            "finance_holding_snapshot_id": f"{holding['source']}:{now[:10]}:{holding['source_holding_id']}",
            "source_holding_snapshot_id": f"{now[:10]}:{holding['source_holding_id']}",
            "date": now[:10],
        }
        out.append(
            {
                "finance_holding_snapshot_id": snapshot_row["finance_holding_snapshot_id"],
                "date": snapshot_row["date"],
                "source": holding["source"],
                "source_holding_snapshot_id": snapshot_row["source_holding_snapshot_id"],
                "finance_holding_id": holding["finance_holding_id"],
                "source_holding_id": holding["source_holding_id"],
                "finance_account_id": holding["finance_account_id"],
                "source_account_id": holding["source_account_id"],
                "owner": holding["owner"],
                "account_group": holding["account_group"],
                "institution_name": holding.get("institution_name"),
                "account_name": holding.get("account_name"),
                "security_id": holding.get("security_id"),
                "security_name": holding.get("security_name"),
                "ticker": holding.get("ticker"),
                "type": holding.get("type"),
                "quantity": holding.get("quantity"),
                "cost_basis": holding.get("cost_basis"),
                "price": holding.get("price"),
                "value": holding.get("value"),
                "as_of": holding.get("as_of"),
                "raw_json": holding.get("raw_json"),
            }
        )
    return out


def _net_worth_values(account: dict[str, Any]) -> tuple[float, float]:
    if not account.get("include_in_net_worth"):
        return 0.0, 0.0
    balance = _coerce_float(account.get("current_balance"))
    if account.get("account_group") == "credit_card":
        debt = abs(balance)
        return -debt, debt
    if balance < 0:
        return balance, abs(balance)
    return balance, 0.0


def _snapshot_rows(accounts: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    day = now[:10]
    rows = []
    for account in accounts:
        net_value, debt = _net_worth_values(account)
        rows.append(
            {
                "snapshot_id": f"{day}:{account['finance_account_id']}",
                "date": day,
                "finance_account_id": account["finance_account_id"],
                "source": account["source"],
                "source_account_id": account["source_account_id"],
                "owner": account["owner"],
                "account_group": account["account_group"],
                "institution_name": account.get("institution_name"),
                "account_name": account.get("account_name"),
                "balance": _coerce_float(account.get("current_balance")),
                "net_worth_value": net_value,
                "debt_value": debt,
                "iso_currency_code": account.get("iso_currency_code"),
                "include_in_net_worth": _coerce_int(account.get("include_in_net_worth")),
                "as_of": account.get("as_of") or now,
            }
        )
    return rows


def _blank_cashflow_row(day: str, owner: str) -> dict[str, Any]:
    return {
        "date": day,
        "owner": owner,
        "income": 0.0,
        "spending": 0.0,
        "net": 0.0,
        "parent_draw": 0.0,
        "credit_card_payments": 0.0,
        "internal_transfers": 0.0,
        "txn_count": 0,
    }


def _apply_cashflow_amount(row: dict[str, Any], amount: float) -> None:
    if amount > 0:
        row["spending"] += amount
    elif amount < 0:
        row["income"] += -amount
    row["net"] += -amount
    row["txn_count"] += 1


def _materialize_cashflow(t: Tracker) -> None:
    transactions = _read_rows(
        t,
        """
        SELECT tx.*, a.institution_name, a.account_name
        FROM finance_transactions tx
        LEFT JOIN finance_accounts a ON a.finance_account_id = tx.finance_account_id
        WHERE tx.date IS NOT NULL
          AND COALESCE(tx.pending, 0) = 0
        """,
    )
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    parent_draws = []
    for txn in transactions:
        day = txn.get("date")
        if not day:
            continue
        owner = str(txn.get("owner") or "self").strip() or "self"
        amount = _coerce_float(txn.get("amount"))
        rows = [
            by_key.setdefault((day, owner), _blank_cashflow_row(day, owner)),
            by_key.setdefault((day, "all"), _blank_cashflow_row(day, "all")),
        ]
        is_card_payment = bool(txn.get("is_credit_card_payment"))
        is_transfer = bool(txn.get("is_internal_transfer"))
        parent_draw = _coerce_float(txn.get("parent_draw"))
        if is_card_payment:
            for row in rows:
                row["credit_card_payments"] += max(amount, 0.0)
            continue
        if parent_draw > 0:
            for row in rows:
                row["parent_draw"] += parent_draw
            parent_draws.append(
                {
                    "finance_transaction_id": txn["finance_transaction_id"],
                    "source": txn["source"],
                    "source_transaction_id": txn["source_transaction_id"],
                    "date": day,
                    "owner": owner,
                    "finance_account_id": txn["finance_account_id"],
                    "source_account_id": txn["source_account_id"],
                    "institution": txn.get("institution_name"),
                    "account_name": txn.get("account_name"),
                    "merchant_name": txn.get("merchant_name"),
                    "name": txn.get("name"),
                    "amount": parent_draw,
                    "category": txn.get("category"),
                }
            )
        if is_transfer:
            for row in rows:
                row["internal_transfers"] += abs(amount)
            continue
        for row in rows:
            _apply_cashflow_amount(row, amount)
    _execute(t, "DELETE FROM finance_daily_cashflow")
    _execute(t, "DELETE FROM finance_parent_draws")
    t.upsert("finance_daily_cashflow", list(by_key.values()), key=["date", "owner"])
    t.upsert("finance_parent_draws", parent_draws, key=["finance_transaction_id"])


def _blank_net_worth_row(day: str, owner: str) -> dict[str, Any]:
    return {
        "date": day,
        "owner": owner,
        "cash": 0.0,
        "investments": 0.0,
        "credit_card_debt": 0.0,
        "other": 0.0,
        "assets": 0.0,
        "debts": 0.0,
        "net_worth": 0.0,
    }


def _materialize_net_worth(t: Tracker) -> None:
    snapshots = _read_rows(
        t,
        """
        SELECT date, owner, account_group, net_worth_value, debt_value
        FROM finance_account_snapshots
        WHERE COALESCE(include_in_net_worth, 0) = 1
        """,
    )
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for snap in snapshots:
        day = snap["date"]
        owner = snap.get("owner") or "self"
        rows = [
            by_key.setdefault((day, owner), _blank_net_worth_row(day, owner)),
            by_key.setdefault((day, "all"), _blank_net_worth_row(day, "all")),
        ]
        group = snap.get("account_group") or "other"
        net_value = _coerce_float(snap.get("net_worth_value"))
        debt = _coerce_float(snap.get("debt_value"))
        for row in rows:
            if group == "cash":
                row["cash"] += net_value
            elif group == "investments":
                row["investments"] += net_value
            elif group == "credit_card":
                row["credit_card_debt"] += debt
            else:
                row["other"] += net_value
            if net_value >= 0:
                row["assets"] += net_value
            else:
                row["debts"] += abs(net_value)
            row["net_worth"] += net_value
    _execute(t, "DELETE FROM finance_daily_net_worth")
    t.upsert("finance_daily_net_worth", list(by_key.values()), key=["date", "owner"])


def _clear_materialized(t: Tracker) -> None:
    _execute(t, "DELETE FROM finance_accounts")
    _execute(t, "DELETE FROM finance_transactions")
    _execute(t, "DELETE FROM finance_holdings")
    _execute(t, "DELETE FROM finance_daily_cashflow")
    _execute(t, "DELETE FROM finance_parent_draws")
    _execute(t, "DELETE FROM finance_daily_net_worth")


def sync(t: Tracker) -> None:
    now = _now_iso()
    account_views = _export_views(t, "_finance_accounts_export")
    if not account_views:
        _clear_materialized(t)
        t.cursor.set(now)
        t.log.info("finance: no source finance export views found")
        return

    accounts = _load_exported_accounts(t)
    account_by_id = _account_map(accounts)
    transactions = _load_exported_transactions(t, account_by_id)
    holdings = _load_exported_holdings(t, account_by_id)
    holding_snapshots = _load_exported_holding_snapshots(t, account_by_id, holdings, now)

    _execute(t, "DELETE FROM finance_accounts")
    _execute(t, "DELETE FROM finance_transactions")
    _execute(t, "DELETE FROM finance_holdings")
    t.upsert("finance_accounts", accounts, key=["finance_account_id"])
    t.upsert("finance_transactions", transactions, key=["finance_transaction_id"])
    t.upsert("finance_holdings", holdings, key=["finance_holding_id"])
    t.upsert("finance_holding_snapshots", holding_snapshots, key=["finance_holding_snapshot_id"])
    t.upsert("finance_account_snapshots", _snapshot_rows(accounts, now), key=["snapshot_id"])
    _materialize_cashflow(t)
    _materialize_net_worth(t)
    t.cursor.set(now)
    t.log.info(
        "finance: %d accounts, %d transactions, %d holdings from %d account export views",
        len(accounts),
        len(transactions),
        len(holdings),
        len(account_views),
    )
