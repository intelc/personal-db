"""Plaid ingest for accounts, transactions, balances, and investments."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
import yaml

from personal_db.db import connect
from personal_db.tracker import Cursor, Tracker

API_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}

SKIPPABLE_INVESTMENT_ERRORS = {
    "ADDITION_LIMIT_EXCEEDED",
    "INSTITUTION_NOT_SUPPORTED",
    "INVALID_PRODUCT",
    "ITEM_NOT_SUPPORTED",
    "NO_INVESTMENT_ACCOUNTS",
    "PRODUCT_NOT_ENABLED",
    "PRODUCT_NOT_READY",
    "PRODUCTS_NOT_SUPPORTED",
}

ACCOUNT_GROUPS = {"cash", "credit_card", "investments", "other"}
SELF_OWNERS = {"self", "me", "personal"}
CREDIT_CARD_PAYMENT_DETAIL = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"


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


def _api_host(t: Tracker) -> str:
    env = (_env(t, "PLAID_ENV", "development") or "development").strip().lower()
    if env not in API_HOSTS:
        raise RuntimeError("PLAID_ENV must be one of: sandbox, development, production")
    return API_HOSTS[env]


def _credentials(t: Tracker) -> tuple[str, str]:
    client_id = _env(t, "PLAID_CLIENT_ID")
    secret = _env(t, "PLAID_SECRET")
    if not client_id or not secret:
        raise RuntimeError("Set PLAID_CLIENT_ID and PLAID_SECRET")
    return client_id, secret


def _post(t: Tracker, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    client_id, secret = _credentials(t)
    body = {"client_id": client_id, "secret": secret, **payload}
    r = requests.post(f"{_api_host(t)}{path}", json=body, timeout=45)
    if r.status_code >= 400:
        try:
            err = r.json()
        except ValueError:
            r.raise_for_status()
        code = err.get("error_code") or err.get("error_type") or r.status_code
        message = err.get("error_message") or err
        raise RuntimeError(f"Plaid {path} error {code}: {message}")
    return r.json()


def _state_path(t: Tracker) -> Path:
    path = t.cfg.state_dir / "plaid" / "items.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _latest_backup_path(t: Tracker) -> Path | None:
    backup_dir = t.cfg.state_dir / "plaid" / "backups"
    if not backup_dir.exists():
        return None
    backups = sorted(backup_dir.glob("items-*.json"), key=lambda p: p.stat().st_mtime)
    return backups[-1] if backups else None


def _load_items(t: Tracker) -> list[dict[str, Any]]:
    path = _state_path(t)
    source = path if path.exists() else _latest_backup_path(t)
    if source is None:
        return []
    try:
        data = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError):
        backup = _latest_backup_path(t)
        if backup is None or backup == source:
            raise
        t.log.warning("plaid item state unreadable at %s; using backup %s", source, backup)
        data = json.loads(backup.read_text())
    if source != path:
        t.log.warning("plaid item state missing; using token backup %s", source)
    items = data.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError(f"Malformed Plaid item state at {path}")
    return [item for item in items if item.get("access_token")]


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _read_rows(t: Tracker, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    con = connect(t.cfg.db_path)
    cur = con.execute(sql, params)
    cols = [desc[0] for desc in cur.description]
    rows = [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    con.close()
    return rows


def _execute(t: Tracker, sql: str, params: tuple[Any, ...] = ()) -> None:
    con = connect(t.cfg.db_path)
    con.execute(sql, params)
    con.commit()
    con.close()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _item_label(item: dict[str, Any]) -> str:
    return item.get("institution_name") or item.get("institution_id") or item.get("item_id") or "unknown"


def _account_group(account: dict[str, Any]) -> str:
    typ = (account.get("type") or "").lower()
    subtype = (account.get("subtype") or "").lower()
    if typ == "investment":
        return "investments"
    if typ == "credit":
        return "credit_card"
    if typ == "depository" and subtype in {
        "checking",
        "savings",
        "money market",
        "cash management",
        "prepaid",
    }:
        return "cash"
    return "other"


def _account_display_label(account: dict[str, Any]) -> str:
    institution = account.get("institution_name") or "Unknown institution"
    name = account.get("official_name") or account.get("name") or "Unknown account"
    mask = account.get("mask")
    suffix = f" ...{mask}" if mask and str(mask) not in str(name) else ""
    return f"{institution} - {name}{suffix}"


def _label_config_path(t: Tracker) -> Path:
    tracker_dir = t.cfg.trackers_dir / "plaid"
    tracker_dir.mkdir(parents=True, exist_ok=True)
    return tracker_dir / "account_labels.yaml"


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def _load_account_labels(t: Tracker, accounts: list[dict[str, Any]], now: str) -> dict[str, dict[str, Any]]:
    """Load or seed editable account ownership/group labels.

    The file is deliberately outside the four canonical tracker files so
    `tracker reinstall plaid` can refresh code without overwriting ownership
    decisions such as `owner: parents`.
    """
    path = _label_config_path(t)
    data: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text()) or {}
        if isinstance(loaded, dict):
            data = loaded
    accounts_cfg = data.get("accounts")
    if not isinstance(accounts_cfg, dict):
        accounts_cfg = {}

    changed = False
    for account in accounts:
        account_id = account.get("account_id")
        if not account_id:
            continue
        current = accounts_cfg.get(account_id)
        if not isinstance(current, dict):
            current = {}
        seeded = {
            "export_enabled": _coerce_bool(current.get("export_enabled"), True),
            "owner": str(current.get("owner") or "self").strip() or "self",
            "account_group": str(current.get("account_group") or _account_group(account)).strip(),
            "label": current.get("label") or _account_display_label(account),
            "include_in_net_worth": _coerce_bool(current.get("include_in_net_worth"), True),
            "parent_draw_source": _coerce_bool(current.get("parent_draw_source"), False),
            "notes": current.get("notes") or "",
        }
        if seeded["account_group"] not in ACCOUNT_GROUPS:
            seeded["account_group"] = _account_group(account)
        if current != seeded:
            accounts_cfg[account_id] = seeded
            changed = True

    if not path.exists() or changed:
        data = {
            "notes": [
                "Set export_enabled to false to keep an account out of downstream finance views.",
                "Edit owner to 'parents' for accounts you manage for parents.",
                "Supported account_group values: cash, credit_card, investments, other.",
                "Credit-card payments and internal transfers are excluded from cashflow.",
            ],
            "accounts": accounts_cfg,
        }
        text = yaml.safe_dump(data, sort_keys=True, allow_unicode=False)
        path.write_text(text)
        path.chmod(0o600)

    labels: dict[str, dict[str, Any]] = {}
    for account in accounts:
        account_id = account.get("account_id")
        if not account_id:
            continue
        cfg = accounts_cfg.get(account_id) or {}
        owner = str(cfg.get("owner") or "self").strip() or "self"
        group = str(cfg.get("account_group") or _account_group(account)).strip()
        if group not in ACCOUNT_GROUPS:
            group = _account_group(account)
        labels[account_id] = {
            "account_id": account_id,
            "export_enabled": 1 if _coerce_bool(cfg.get("export_enabled"), True) else 0,
            "owner": owner,
            "account_group": group,
            "label": cfg.get("label") or _account_display_label(account),
            "include_in_net_worth": 1 if _coerce_bool(cfg.get("include_in_net_worth"), True) else 0,
            "parent_draw_source": 1 if _coerce_bool(cfg.get("parent_draw_source"), False) else 0,
            "notes": cfg.get("notes") or "",
            "updated_at": now,
        }
    return labels


def _materialize_account_labels(
    t: Tracker,
    labels: dict[str, dict[str, Any]],
) -> None:
    label_rows = [{k: v for k, v in row.items() if k != "export_enabled"} for row in labels.values()]
    export_rows = [
        {
            "account_id": row["account_id"],
            "export_enabled": row["export_enabled"],
            "updated_at": row["updated_at"],
        }
        for row in labels.values()
    ]
    t.upsert("plaid_account_labels", label_rows, key=["account_id"])
    t.upsert("plaid_account_exports", export_rows, key=["account_id"])


def _fetch_item(t: Tracker, item: dict[str, Any], now: str) -> dict[str, Any]:
    body = _post(t, "/item/get", {"access_token": item["access_token"]})
    plaid_item = body.get("item") or {}
    return {
        "item_id": plaid_item.get("item_id") or item.get("item_id"),
        "institution_id": plaid_item.get("institution_id") or item.get("institution_id"),
        "institution_name": item.get("institution_name"),
        "webhook": plaid_item.get("webhook"),
        "products": _json(plaid_item.get("products")),
        "available_products": _json(plaid_item.get("available_products")),
        "billed_products": _json(plaid_item.get("billed_products")),
        "consent_expiration_time": plaid_item.get("consent_expiration_time"),
        "error_json": _json(plaid_item.get("error")),
        "created_at": item.get("created_at"),
        "updated_at": now,
    }


def _flatten_account(
    account: dict[str, Any],
    *,
    item: dict[str, Any],
    institution_name: str | None,
    balance_mode: str,
    fetched_at: str,
) -> dict[str, Any]:
    balances = account.get("balances") or {}
    return {
        "account_id": account.get("account_id"),
        "item_id": item.get("item_id"),
        "institution_name": institution_name,
        "name": account.get("name"),
        "official_name": account.get("official_name"),
        "mask": account.get("mask"),
        "type": account.get("type"),
        "subtype": account.get("subtype"),
        "verification_status": account.get("verification_status"),
        "current_balance": balances.get("current"),
        "available_balance": balances.get("available"),
        "limit_balance": balances.get("limit"),
        "iso_currency_code": balances.get("iso_currency_code"),
        "unofficial_currency_code": balances.get("unofficial_currency_code"),
        "balance_mode": balance_mode,
        "balance_as_of": fetched_at,
        "raw_json": _json(account),
    }


def _sync_accounts(t: Tracker, item: dict[str, Any], now: str) -> int:
    mode = (_env(t, "PLAID_BALANCE_MODE", "cached") or "cached").strip().lower()
    if mode not in {"cached", "real_time"}:
        raise RuntimeError("PLAID_BALANCE_MODE must be cached or real_time")
    path = "/accounts/balance/get" if mode == "real_time" else "/accounts/get"
    body = _post(t, path, {"access_token": item["access_token"]})
    rows = [
        _flatten_account(
            account,
            item=item,
            institution_name=_item_label(item),
            balance_mode=mode,
            fetched_at=now,
        )
        for account in body.get("accounts") or []
    ]
    return t.upsert("plaid_accounts", rows, key=["account_id"])


def _flatten_transaction(txn: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    pfc = txn.get("personal_finance_category") or {}
    return {
        "transaction_id": txn["transaction_id"],
        "item_id": item["item_id"],
        "account_id": txn.get("account_id"),
        "date": txn.get("date"),
        "authorized_date": txn.get("authorized_date"),
        "datetime": txn.get("datetime"),
        "authorized_datetime": txn.get("authorized_datetime"),
        "name": txn.get("name"),
        "merchant_name": txn.get("merchant_name"),
        "amount": txn.get("amount"),
        "iso_currency_code": txn.get("iso_currency_code"),
        "unofficial_currency_code": txn.get("unofficial_currency_code"),
        "pending": 1 if txn.get("pending") else 0,
        "pending_transaction_id": txn.get("pending_transaction_id"),
        "payment_channel": txn.get("payment_channel"),
        "category": _json(txn.get("category")),
        "personal_finance_primary": pfc.get("primary"),
        "personal_finance_detailed": pfc.get("detailed"),
        "personal_finance_confidence": pfc.get("confidence_level"),
        "check_number": txn.get("check_number"),
        "website": txn.get("website"),
        "logo_url": txn.get("logo_url"),
        "removed_at": None,
        "raw_json": _json(txn),
    }


def _mark_removed_transactions(t: Tracker, removed: list[dict[str, Any]], removed_at: str) -> int:
    if not removed:
        return 0
    con = connect(t.cfg.db_path)
    con.executemany(
        "UPDATE plaid_transactions SET removed_at=? WHERE transaction_id=?",
        [(removed_at, r.get("transaction_id")) for r in removed if r.get("transaction_id")],
    )
    con.commit()
    changed = con.total_changes
    con.close()
    return changed


def _post_transactions_sync(t: Tracker, access_token: str, cursor: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"access_token": access_token, "count": 500}
    if cursor:
        payload["cursor"] = cursor
    try:
        return _post(t, "/transactions/sync", payload)
    except RuntimeError as exc:
        if "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" in str(exc):
            raise
        if "PRODUCT_NOT_READY" in str(exc):
            return {"added": [], "modified": [], "removed": [], "has_more": False, "next_cursor": cursor}
        raise


def _sync_transactions(t: Tracker, item: dict[str, Any], now: str) -> tuple[int, int]:
    cursor_store = Cursor(f"plaid:transactions:{item['item_id']}", t.cfg.state_dir)
    original_cursor = cursor_store.get()
    cursor = original_cursor
    added_or_modified: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    while True:
        try:
            body = _post_transactions_sync(t, item["access_token"], cursor)
        except RuntimeError as exc:
            if "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION" not in str(exc):
                raise
            cursor = original_cursor
            added_or_modified = []
            removed = []
            body = _post_transactions_sync(t, item["access_token"], cursor)

        added_or_modified.extend(body.get("added") or [])
        added_or_modified.extend(body.get("modified") or [])
        removed.extend(body.get("removed") or [])
        cursor = body.get("next_cursor") or cursor
        if not body.get("has_more"):
            break

    rows = [_flatten_transaction(txn, item) for txn in added_or_modified]
    upserts = t.upsert("plaid_transactions", rows, key=["transaction_id"])
    removals = _mark_removed_transactions(t, removed, now)
    if cursor:
        cursor_store.set(cursor)
    return upserts, removals


def _flatten_security(security: dict[str, Any]) -> dict[str, Any]:
    return {
        "security_id": security.get("security_id"),
        "name": security.get("name"),
        "ticker_symbol": security.get("ticker_symbol"),
        "type": security.get("type"),
        "subtype": security.get("subtype"),
        "close_price": security.get("close_price"),
        "close_price_as_of": security.get("close_price_as_of"),
        "iso_currency_code": security.get("iso_currency_code"),
        "unofficial_currency_code": security.get("unofficial_currency_code"),
        "raw_json": _json(security),
    }


def _flatten_holding(holding: dict[str, Any], item: dict[str, Any], as_of: str) -> dict[str, Any]:
    account_id = holding.get("account_id") or ""
    security_id = holding.get("security_id") or "cash"
    return {
        "snapshot_id": f"{item['item_id']}:{account_id}:{security_id}:{as_of}",
        "item_id": item["item_id"],
        "account_id": account_id,
        "security_id": holding.get("security_id"),
        "as_of": as_of,
        "quantity": holding.get("quantity"),
        "cost_basis": holding.get("cost_basis"),
        "institution_price": holding.get("institution_price"),
        "institution_price_as_of": holding.get("institution_price_as_of"),
        "institution_price_datetime": holding.get("institution_price_datetime"),
        "institution_value": holding.get("institution_value"),
        "iso_currency_code": holding.get("iso_currency_code"),
        "unofficial_currency_code": holding.get("unofficial_currency_code"),
        "raw_json": _json(holding),
    }


def _is_skippable_investment_error(exc: RuntimeError) -> bool:
    return any(code in str(exc) for code in SKIPPABLE_INVESTMENT_ERRORS)


def _sync_investment_holdings(t: Tracker, item: dict[str, Any], now: str) -> tuple[int, int, int]:
    try:
        body = _post(t, "/investments/holdings/get", {"access_token": item["access_token"]})
    except RuntimeError as exc:
        if _is_skippable_investment_error(exc):
            t.log.info("plaid investments skipped for %s: %s", _item_label(item), exc)
            return 0, 0, 0
        raise

    account_rows = [
        _flatten_account(
            account,
            item=item,
            institution_name=_item_label(item),
            balance_mode="investments",
            fetched_at=now,
        )
        for account in body.get("accounts") or []
    ]
    holding_rows = [_flatten_holding(h, item, now) for h in body.get("holdings") or []]
    security_rows = [_flatten_security(s) for s in body.get("securities") or []]
    return (
        t.upsert("plaid_accounts", account_rows, key=["account_id"]),
        t.upsert("plaid_investment_holdings", holding_rows, key=["snapshot_id"]),
        t.upsert("plaid_investment_securities", security_rows, key=["security_id"]),
    )


def _flatten_investment_transaction(txn: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "investment_transaction_id": txn.get("investment_transaction_id"),
        "item_id": item["item_id"],
        "account_id": txn.get("account_id"),
        "security_id": txn.get("security_id"),
        "date": txn.get("date"),
        "name": txn.get("name"),
        "type": txn.get("type"),
        "subtype": txn.get("subtype"),
        "amount": txn.get("amount"),
        "quantity": txn.get("quantity"),
        "price": txn.get("price"),
        "fees": txn.get("fees"),
        "iso_currency_code": txn.get("iso_currency_code"),
        "unofficial_currency_code": txn.get("unofficial_currency_code"),
        "cancel_transaction_id": txn.get("cancel_transaction_id"),
        "raw_json": _json(txn),
    }


def _investment_start_date(t: Tracker, item_id: str, start: str | None = None) -> str:
    if start:
        return start
    cursor = Cursor(f"plaid:investment_transactions:{item_id}", t.cfg.state_dir).get()
    if cursor:
        try:
            return (date.fromisoformat(cursor) - timedelta(days=7)).isoformat()
        except ValueError:
            pass
    return (date.today() - timedelta(days=730)).isoformat()


def _sync_investment_transactions(
    t: Tracker,
    item: dict[str, Any],
    *,
    start: str | None = None,
    end: str | None = None,
) -> int:
    start_date = _investment_start_date(t, item["item_id"], start)
    end_date = end or date.today().isoformat()
    offset = 0
    rows: list[dict[str, Any]] = []
    while True:
        try:
            body = _post(
                t,
                "/investments/transactions/get",
                {
                    "access_token": item["access_token"],
                    "start_date": start_date,
                    "end_date": end_date,
                    "options": {"count": 500, "offset": offset},
                },
            )
        except RuntimeError as exc:
            if _is_skippable_investment_error(exc):
                return 0
            raise
        txns = body.get("investment_transactions") or []
        rows.extend(_flatten_investment_transaction(txn, item) for txn in txns)
        total = body.get("total_investment_transactions") or len(rows)
        offset += len(txns)
        if not txns or offset >= total:
            break

    written = t.upsert(
        "plaid_investment_transactions",
        rows,
        key=["investment_transaction_id"],
    )
    Cursor(f"plaid:investment_transactions:{item['item_id']}", t.cfg.state_dir).set(end_date)
    return written


def _current_accounts(t: Tracker) -> list[dict[str, Any]]:
    return _read_rows(
        t,
        """
        SELECT account_id, item_id, institution_name, name, official_name, mask, type, subtype,
               current_balance, available_balance, iso_currency_code, balance_as_of
        FROM plaid_accounts
        WHERE account_id IS NOT NULL
        """,
    )


def _account_balance(account: dict[str, Any]) -> float:
    try:
        return float(account.get("current_balance") or 0)
    except (TypeError, ValueError):
        return 0.0


def _snapshot_values(account: dict[str, Any], label: dict[str, Any]) -> tuple[float, float]:
    balance = _account_balance(account)
    if not label.get("include_in_net_worth"):
        return 0.0, 0.0
    group = label.get("account_group") or _account_group(account)
    if group == "credit_card":
        # Plaid reports credit-card balances as positive liabilities.
        return -balance, max(balance, 0.0)
    if balance < 0:
        return balance, abs(balance)
    return balance, 0.0


def _materialize_account_snapshots(
    t: Tracker,
    accounts: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    now: str,
) -> None:
    snapshot_date = now[:10]
    rows = []
    for account in accounts:
        account_id = account.get("account_id")
        if not account_id:
            continue
        label = labels.get(account_id) or {}
        balance = _account_balance(account)
        net_worth_value, debt_value = _snapshot_values(account, label)
        rows.append(
            {
                "snapshot_id": f"{snapshot_date}:{account_id}",
                "date": snapshot_date,
                "account_id": account_id,
                "owner": label.get("owner") or "self",
                "account_group": label.get("account_group") or _account_group(account),
                "institution_name": account.get("institution_name"),
                "account_name": account.get("official_name") or account.get("name"),
                "balance": balance,
                "net_worth_value": net_worth_value,
                "debt_value": debt_value,
                "iso_currency_code": account.get("iso_currency_code"),
                "as_of": account.get("balance_as_of") or now,
            }
        )
    t.upsert("plaid_account_snapshots", rows, key=["snapshot_id"])


def _is_credit_card_payment(txn: dict[str, Any]) -> bool:
    detailed = (txn.get("personal_finance_detailed") or "").upper()
    primary = (txn.get("personal_finance_primary") or "").upper()
    name = f"{txn.get('name') or ''} {txn.get('merchant_name') or ''}".upper()
    if detailed == CREDIT_CARD_PAYMENT_DETAIL:
        return True
    if "CREDIT_CARD_PAYMENT" in detailed:
        return True
    return primary == "LOAN_PAYMENTS" and ("PAYMENT" in name or "AUTOPAY" in name)


def _is_internal_transfer(txn: dict[str, Any]) -> bool:
    primary = (txn.get("personal_finance_primary") or "").upper()
    detailed = (txn.get("personal_finance_detailed") or "").upper()
    return primary.startswith("TRANSFER_") or detailed.startswith("TRANSFER_")


def _is_parent_account(label: dict[str, Any]) -> bool:
    owner = str(label.get("owner") or "self").strip().lower()
    return bool(label.get("parent_draw_source")) or owner not in SELF_OWNERS


def _cashflow_transactions(t: Tracker) -> list[dict[str, Any]]:
    return _read_rows(
        t,
        """
        SELECT t.transaction_id, t.account_id, t.date, t.name, t.merchant_name,
               t.amount, t.pending, t.removed_at, t.personal_finance_primary,
               t.personal_finance_detailed, a.institution_name, a.name AS account_name
        FROM plaid_transactions t
        LEFT JOIN plaid_accounts a ON a.account_id = t.account_id
        WHERE t.date IS NOT NULL
          AND t.removed_at IS NULL
          AND COALESCE(t.pending, 0) = 0
        """,
    )


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


def _materialize_cashflow(
    t: Tracker,
    labels: dict[str, dict[str, Any]],
) -> None:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    parent_draws: list[dict[str, Any]] = []

    for txn in _cashflow_transactions(t):
        day = txn.get("date")
        if not day:
            continue
        label = labels.get(txn.get("account_id")) or {"owner": "self", "account_group": "other"}
        owner = str(label.get("owner") or "self").strip() or "self"
        amount = float(txn.get("amount") or 0)
        rows = [
            by_key.setdefault((day, owner), _blank_cashflow_row(day, owner)),
            by_key.setdefault((day, "all"), _blank_cashflow_row(day, "all")),
        ]
        is_card_payment = _is_credit_card_payment(txn)
        is_transfer = _is_internal_transfer(txn)
        is_parent_draw = _is_parent_account(label) and amount > 0 and not is_card_payment

        if is_card_payment:
            for row in rows:
                row["credit_card_payments"] += max(amount, 0.0)
            continue

        if is_parent_draw:
            for row in rows:
                row["parent_draw"] += amount
            parent_draws.append(
                {
                    "transaction_id": txn["transaction_id"],
                    "date": day,
                    "owner": owner,
                    "account_id": txn.get("account_id"),
                    "institution": txn.get("institution_name"),
                    "account_name": txn.get("account_name"),
                    "merchant_name": txn.get("merchant_name"),
                    "name": txn.get("name"),
                    "amount": amount,
                    "category": txn.get("personal_finance_detailed")
                    or txn.get("personal_finance_primary"),
                }
            )

        if is_transfer:
            for row in rows:
                row["internal_transfers"] += abs(amount)
            continue

        for row in rows:
            _apply_cashflow_amount(row, amount)

    _execute(t, "DELETE FROM plaid_daily_cashflow")
    _execute(t, "DELETE FROM plaid_parent_draws")
    t.upsert("plaid_daily_cashflow", list(by_key.values()), key=["date", "owner"])
    t.upsert("plaid_parent_draws", parent_draws, key=["transaction_id"])


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
        FROM plaid_account_snapshots
        """,
    )
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for snap in snapshots:
        day = snap["date"]
        owner = snap["owner"] or "self"
        rows = [
            by_key.setdefault((day, owner), _blank_net_worth_row(day, owner)),
            by_key.setdefault((day, "all"), _blank_net_worth_row(day, "all")),
        ]
        group = snap.get("account_group") or "other"
        net_value = float(snap.get("net_worth_value") or 0)
        debt = float(snap.get("debt_value") or 0)
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
    _execute(t, "DELETE FROM plaid_daily_net_worth")
    t.upsert("plaid_daily_net_worth", list(by_key.values()), key=["date", "owner"])


def _materialize_finance_model(t: Tracker, now: str) -> None:
    accounts = _current_accounts(t)
    labels = _load_account_labels(t, accounts, now)
    _materialize_account_labels(t, labels)
    _materialize_account_snapshots(t, accounts, labels, now)
    _materialize_cashflow(t, labels)
    _materialize_net_worth(t)


def sync(t: Tracker) -> None:
    items = _load_items(t)
    if not items:
        t.log.info("plaid: no linked Items yet; run the link_item action from setup instructions")
        return

    now = _now_iso()
    item_rows = []
    total_accounts = total_txns = total_removed = total_holdings = total_securities = 0
    total_investment_txns = 0
    for item in items:
        item_rows.append(_fetch_item(t, item, now))
        total_accounts += _sync_accounts(t, item, now)
        txn_count, removed_count = _sync_transactions(t, item, now)
        total_txns += txn_count
        total_removed += removed_count
        _, holdings, securities = _sync_investment_holdings(t, item, now)
        total_holdings += holdings
        total_securities += securities
        total_investment_txns += _sync_investment_transactions(t, item)

    t.upsert("plaid_items", item_rows, key=["item_id"])
    _materialize_finance_model(t, now)
    t.cursor.set(now)
    t.log.info(
        "plaid: %d items, %d account changes, %d transaction changes, "
        "%d removals, %d holding snapshots, %d securities, %d investment txns",
        len(items),
        total_accounts,
        total_txns,
        total_removed,
        total_holdings,
        total_securities,
        total_investment_txns,
    )


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    items = _load_items(t)
    if not items:
        return
    for item in items:
        _sync_investment_transactions(t, item, start=start, end=end)
    sync(t)
