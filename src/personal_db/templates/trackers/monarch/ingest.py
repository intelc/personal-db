"""Monarch Money ingest.

This tracker keeps Monarch data in `monarch_*` source tables. Account labels
and export selection are materialized from `account_exports.yaml` into split
tables so downstream finance reads the same source contract as Plaid.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from personal_db.db import connect
from personal_db.tracker import Tracker

ACCOUNT_GROUPS = {"cash", "credit_card", "investments", "other"}
PARENT_OWNERS = {"parents"}


def _load_sibling(name: str):
    here = Path(__file__).parent
    spec = _ilu.spec_from_file_location(f"_pdb_monarch_{name}", here / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"monarch: cannot load sibling {name}.py from {here}")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_client_mod = _load_sibling("parsers")
MonarchClient = _client_mod.MonarchClient


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _read_env_file(root: Path) -> dict[str, str]:
    env_path = root / ".env"
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _env(t: Tracker, name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or _read_env_file(t.cfg.root).get(name) or default


def _session_path(t: Tracker) -> Path:
    path = t.cfg.state_dir / "monarch" / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    return path


def _client(t: Tracker):
    path = _session_path(t)
    if not path.exists():
        raise RuntimeError("Monarch is not logged in; use the Monarch setup page login action")
    mm = MonarchClient(
        session_file=path,
        timeout=30,
        email=_env(t, "MONARCH_EMAIL"),
        password=_env(t, "MONARCH_PASSWORD"),
        totp_secret=_env(t, "MONARCH_TOTP_SECRET"),
    )
    mm.load_session()
    return mm


def _account_group(account: dict[str, Any]) -> str:
    typ = ((account.get("type") or {}).get("name") or "").lower()
    subtype = ((account.get("subtype") or {}).get("name") or "").lower()
    if typ in {"investment", "brokerage"} or subtype in {"brokerage", "ira", "roth_ira", "401k"}:
        return "investments"
    if typ in {"credit_card", "credit"} or subtype in {"credit_card"}:
        return "credit_card"
    if typ in {"bank", "cash", "depository"} or subtype in {
        "checking",
        "savings",
        "money_market",
        "cash_management",
    }:
        return "cash"
    return "other"


def _owner_flags(owner: str) -> tuple[bool, bool]:
    is_parent = owner.strip().lower() in PARENT_OWNERS
    return (not is_parent, is_parent)


def _exports_path(t: Tracker) -> Path:
    path = t.cfg.trackers_dir / "monarch" / "account_exports.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_exports(t: Tracker) -> dict[str, Any]:
    path = _exports_path(t)
    if not path.exists():
        return {"accounts": {}}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {"accounts": {}}


def _write_exports(t: Tracker, data: dict[str, Any]) -> None:
    path = _exports_path(t)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=False))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _seed_exports(t: Tracker, accounts: list[dict[str, Any]], now: str) -> dict[str, dict[str, Any]]:
    data = _read_exports(t)
    exports = data.get("accounts")
    if not isinstance(exports, dict):
        exports = {}
    changed = False
    for account in accounts:
        account_id = account.get("id")
        if not account_id:
            continue
        existing = exports.get(account_id)
        if not isinstance(existing, dict):
            existing = {}
        owner = str(existing.get("owner") or "self").strip() or "self"
        include_in_net_worth, parent_draw_source = _owner_flags(owner)
        seeded = {
            "export_enabled": bool(existing.get("export_enabled", False)),
            "label": str(existing.get("label") or "").strip(),
            "owner": owner,
            "account_group": str(existing.get("account_group") or _account_group(account)).strip(),
            "include_in_net_worth": include_in_net_worth,
            "parent_draw_source": parent_draw_source,
        }
        if seeded["account_group"] not in ACCOUNT_GROUPS:
            seeded["account_group"] = _account_group(account)
        if seeded != existing:
            exports[account_id] = seeded
            changed = True
    if changed or not _exports_path(t).exists():
        data["notes"] = data.get("notes") or [
            "Enable only Monarch accounts that should appear in combined finance views.",
            "owner self: included in net worth.",
            "owner parents: excluded from net worth; positive outflows count as parent draw.",
        ]
        data["accounts"] = exports
        _write_exports(t, data)
    rows = {}
    for account_id, item in exports.items():
        owner = str(item.get("owner") or "self").strip() or "self"
        include_in_net_worth, parent_draw_source = _owner_flags(owner)
        group = str(item.get("account_group") or "other").strip()
        if group not in ACCOUNT_GROUPS:
            group = "other"
        label = str(item.get("label") or "").strip() or None
        rows[account_id] = {
            "account_id": account_id,
            "export_enabled": 1 if item.get("export_enabled") else 0,
            "label": label,
            "owner": owner,
            "account_group": group,
            "include_in_net_worth": 1 if include_in_net_worth else 0,
            "parent_draw_source": 1 if parent_draw_source else 0,
            "updated_at": now,
        }
    return rows


def _materialize_account_settings(t: Tracker, settings: dict[str, dict[str, Any]]) -> None:
    label_rows = [
        {k: v for k, v in row.items() if k != "export_enabled"}
        for row in settings.values()
    ]
    export_rows = [
        {
            "account_id": row["account_id"],
            "export_enabled": row["export_enabled"],
            "updated_at": row["updated_at"],
        }
        for row in settings.values()
    ]
    t.upsert("monarch_account_labels", label_rows, key=["account_id"])
    t.upsert("monarch_account_exports", export_rows, key=["account_id"])


def _flatten_account(account: dict[str, Any], now: str) -> dict[str, Any]:
    typ = account.get("type") or {}
    subtype = account.get("subtype") or {}
    institution = account.get("institution") or {}
    credential = account.get("credential") or {}
    return {
        "account_id": account.get("id"),
        "display_name": account.get("displayName"),
        "mask": account.get("mask"),
        "type_name": typ.get("name"),
        "type_display": typ.get("display"),
        "subtype_name": subtype.get("name"),
        "subtype_display": subtype.get("display"),
        "institution_id": institution.get("id") or (credential.get("institution") or {}).get("id"),
        "institution_name": institution.get("name") or (credential.get("institution") or {}).get("name"),
        "credential_id": credential.get("id"),
        "data_provider": account.get("dataProvider") or credential.get("dataProvider"),
        "data_provider_account_id": account.get("dataProviderAccountId"),
        "current_balance": account.get("currentBalance"),
        "display_balance": account.get("displayBalance"),
        "include_in_net_worth": 1 if account.get("includeInNetWorth") else 0,
        "include_balance_in_net_worth": 1 if account.get("includeBalanceInNetWorth") else 0,
        "hide_from_list": 1 if account.get("hideFromList") else 0,
        "hide_transactions_from_reports": 1 if account.get("hideTransactionsFromReports") else 0,
        "is_hidden": 1 if account.get("isHidden") else 0,
        "is_asset": 1 if account.get("isAsset") else 0,
        "is_manual": 1 if account.get("isManual") else 0,
        "sync_disabled": 1 if account.get("syncDisabled") else 0,
        "transactions_count": account.get("transactionsCount"),
        "holdings_count": account.get("holdingsCount"),
        "display_last_updated_at": account.get("displayLastUpdatedAt"),
        "updated_at": now,
        "raw_json": _json(account),
    }


def _flatten_transaction(txn: dict[str, Any]) -> dict[str, Any]:
    account = txn.get("account") or {}
    merchant = txn.get("merchant") or {}
    category = txn.get("category") or {}
    return {
        "transaction_id": txn.get("id"),
        "account_id": account.get("id"),
        "account_name": account.get("displayName"),
        "date": txn.get("date"),
        "amount": txn.get("amount"),
        "pending": 1 if txn.get("pending") else 0,
        "merchant_id": merchant.get("id"),
        "merchant_name": merchant.get("name"),
        "category_id": category.get("id"),
        "category_name": category.get("name"),
        "hide_from_reports": 1 if txn.get("hideFromReports") else 0,
        "needs_review": 1 if txn.get("needsReview") else 0,
        "review_status": txn.get("reviewStatus"),
        "is_recurring": 1 if txn.get("isRecurring") else 0,
        "is_split": 1 if txn.get("isSplitTransaction") else 0,
        "notes": txn.get("notes"),
        "plaid_name": txn.get("plaidName"),
        "created_at": txn.get("createdAt"),
        "updated_at": txn.get("updatedAt"),
        "raw_json": _json(txn),
    }


def _flatten_balances(
    account: dict[str, Any],
    now: str,
    *,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    balances = account.get("recentBalances") or []
    rows = []
    try:
        start_day = date.fromisoformat(start_date)
        end_day = date.fromisoformat(end_date)
    except ValueError:
        start_day = end_day = date.today()
    expected_days = (end_day - start_day).days + 1
    if balances and all(isinstance(item, (int, float)) or item is None for item in balances):
        # Monarch returns an undated numeric series. For recent windows it is
        # daily, but longer or inactive-account windows can be downsampled. Only
        # materialize when the date mapping is exact.
        if expected_days <= 0 or len(balances) != expected_days:
            return []
    for idx, item in enumerate(balances):
        if isinstance(item, (int, float)):
            day = (start_day + timedelta(days=idx)).isoformat()
            balance = item
        elif isinstance(item, dict):
            day = item.get("date")
            balance = item.get("balance")
        else:
            continue
        if balance is None:
            continue
        if not day:
            continue
        rows.append(
            {
                "balance_id": f"{account.get('id')}:{day}",
                "account_id": account.get("id"),
                "date": day,
                "balance": balance,
                "updated_at": now,
            }
        )
    return rows

def _flatten_holding(account_id: str, edge: dict[str, Any], now: str) -> dict[str, Any] | None:
    node = edge.get("node") or {}
    if not node:
        return None
    security = node.get("security") or node.get("holdings") or {}
    security_id = security.get("id") or node.get("id")
    return {
        "holding_id": f"{account_id}:{node.get('id') or security_id}",
        "account_id": account_id,
        "security_id": security_id,
        "security_name": security.get("name") or node.get("name"),
        "ticker": security.get("ticker") or node.get("ticker"),
        "type": security.get("type") or node.get("type"),
        "quantity": node.get("quantity"),
        "basis": node.get("basis"),
        "total_value": node.get("totalValue"),
        "closing_price": security.get("closingPrice") or node.get("closingPrice"),
        "current_price": security.get("currentPrice"),
        "last_synced_at": node.get("lastSyncedAt"),
        "updated_at": now,
        "raw_json": _json(node),
    }


def _holding_snapshot_row(row: dict[str, Any], now: str) -> dict[str, Any]:
    return {
        "snapshot_id": f"{now[:10]}:{row['holding_id']}",
        "date": now[:10],
        "holding_id": row["holding_id"],
        "account_id": row["account_id"],
        "security_id": row.get("security_id"),
        "security_name": row.get("security_name"),
        "ticker": row.get("ticker"),
        "type": row.get("type"),
        "quantity": row.get("quantity"),
        "basis": row.get("basis"),
        "total_value": row.get("total_value"),
        "closing_price": row.get("closing_price"),
        "current_price": row.get("current_price"),
        "last_synced_at": row.get("last_synced_at"),
        "fetched_at": now,
        "raw_json": row.get("raw_json"),
    }


def _prune_holdings_for_accounts(
    t: Tracker,
    account_ids: list[str],
    current_holding_ids: set[str],
) -> int:
    if not account_ids:
        return 0
    account_placeholders = ",".join("?" * len(account_ids))
    con = connect(t.cfg.db_path)
    try:
        if current_holding_ids:
            holding_ids = sorted(current_holding_ids)
            holding_placeholders = ",".join("?" * len(holding_ids))
            con.execute(
                f"""
                DELETE FROM monarch_holdings
                WHERE account_id IN ({account_placeholders})
                  AND holding_id NOT IN ({holding_placeholders})
                """,
                [*account_ids, *holding_ids],
            )
        else:
            con.execute(
                f"DELETE FROM monarch_holdings WHERE account_id IN ({account_placeholders})",
                account_ids,
            )
        con.commit()
        return con.total_changes
    finally:
        con.close()


def _fetch_transactions(mm, start: str, end: str) -> list[dict[str, Any]]:
    offset = 0
    limit = 500
    out: list[dict[str, Any]] = []
    while True:
        body = mm.get_transactions(limit=limit, offset=offset, start_date=start, end_date=end)
        txns = ((body.get("allTransactions") or {}).get("results") or [])
        out.extend(txns)
        total = (body.get("allTransactions") or {}).get("totalCount") or len(out)
        offset += len(txns)
        if not txns or offset >= total:
            break
    return out


def _sync(t: Tracker, *, start: str | None = None, end: str | None = None) -> None:
    now = _now_iso()
    mm = _client(t)

    accounts_body = mm.get_accounts()
    accounts = accounts_body.get("accounts") or []
    account_rows = [_flatten_account(account, now) for account in accounts if account.get("id")]
    t.upsert("monarch_accounts", account_rows, key=["account_id"])
    _materialize_account_settings(t, _seed_exports(t, accounts, now))

    days_raw = _env(t, "MONARCH_TRANSACTIONS_DAYS", "730") or "730"
    try:
        days = max(1, min(3650, int(days_raw)))
    except ValueError:
        days = 730
    end_date = end or date.today().isoformat()
    start_date = start or (date.fromisoformat(end_date) - timedelta(days=days)).isoformat()

    txns = _fetch_transactions(mm, start_date, end_date)
    txn_rows = [_flatten_transaction(txn) for txn in txns if txn.get("id")]
    t.upsert("monarch_transactions", txn_rows, key=["transaction_id"])

    try:
        balance_start = max(date.fromisoformat(start_date), date.fromisoformat(end_date) - timedelta(days=364)).isoformat()
    except ValueError:
        balance_start = start_date
    balance_body = mm.get_recent_account_balances(start_date=balance_start)
    balance_rows = []
    for account in balance_body.get("accounts") or []:
        balance_rows.extend(_flatten_balances(account, now, start_date=balance_start, end_date=end_date))
    t.upsert("monarch_account_balances", balance_rows, key=["balance_id"])

    holding_rows = []
    refreshed_holding_account_ids = []
    for account in accounts:
        account_id = account.get("id")
        if not account_id:
            continue
        if not account.get("holdingsCount"):
            refreshed_holding_account_ids.append(account_id)
            continue
        try:
            holdings_body = mm.get_account_holdings(account_id=account_id, day=end_date)
        except Exception as exc:
            t.log.info("monarch holdings skipped for %s: %s", account.get("displayName"), exc)
            continue
        refreshed_holding_account_ids.append(account_id)
        edges = (((holdings_body.get("portfolio") or {}).get("aggregateHoldings") or {}).get("edges") or [])
        for edge in edges:
            row = _flatten_holding(account_id, edge, now)
            if row:
                holding_rows.append(row)
    t.upsert("monarch_holdings", holding_rows, key=["holding_id"])
    t.upsert(
        "monarch_holding_snapshots",
        [_holding_snapshot_row(row, now) for row in holding_rows],
        key=["snapshot_id"],
    )
    pruned_holdings = _prune_holdings_for_accounts(
        t,
        refreshed_holding_account_ids,
        {row["holding_id"] for row in holding_rows},
    )
    t.cursor.set(now)
    t.log.info(
        "monarch: %d accounts, %d transactions, %d balances, %d holdings, %d stale holdings pruned",
        len(account_rows),
        len(txn_rows),
        len(balance_rows),
        len(holding_rows),
        pruned_holdings,
    )


def sync(t: Tracker) -> None:
    _sync(t)


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    _sync(t, start=start, end=end)
