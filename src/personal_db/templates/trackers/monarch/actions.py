"""User-triggered Monarch setup actions."""

from __future__ import annotations

import importlib.util as _ilu
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from personal_db.db import connect

ACCOUNT_GROUPS = {"cash", "credit_card", "investments", "other"}
PARENT_OWNERS = {"parents"}


def _load_sibling(name: str):
    here = Path(__file__).parent
    spec = _ilu.spec_from_file_location(f"_pdb_monarch_actions_{name}", here / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"monarch: cannot load sibling {name}.py from {here}")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_client_mod = _load_sibling("parsers")
MonarchClient = _client_mod.MonarchClient
MonarchMFARequired = _client_mod.MonarchMFARequired


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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _env(cfg, name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or _read_env_file(cfg.root).get(name) or default


def _credentials(cfg) -> tuple[str, str]:
    email = _env(cfg, "MONARCH_EMAIL")
    password = _env(cfg, "MONARCH_PASSWORD")
    if not email or not password:
        raise RuntimeError("Set MONARCH_EMAIL and MONARCH_PASSWORD first")
    return email, password


def _totp_secret(cfg) -> str | None:
    return _env(cfg, "MONARCH_TOTP_SECRET")


def _session_path(cfg) -> Path:
    path = cfg.state_dir / "monarch" / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    return path


def _client(cfg):
    return MonarchClient(
        session_file=_session_path(cfg),
        timeout=30,
        email=_env(cfg, "MONARCH_EMAIL"),
        password=_env(cfg, "MONARCH_PASSWORD"),
        totp_secret=_env(cfg, "MONARCH_TOTP_SECRET"),
    )


def _secure_session_file(cfg) -> None:
    path = _session_path(cfg)
    if path.exists():
        os.chmod(path, 0o600)


def login(cfg) -> dict[str, Any]:
    email, password = _credentials(cfg)
    totp_secret = _totp_secret(cfg)
    mm = _client(cfg)
    try:
        if totp_secret:
            mm.login_with_totp_secret(email=email, password=password, secret=totp_secret)
        else:
            mm.login(email=email, password=password)
    except MonarchMFARequired:
        return {
            "ok": False,
            "mfa_required": True,
            "message": "MFA required. Save MONARCH_TOTP_SECRET in setup, then click Login to Monarch again.",
        }
    _secure_session_file(cfg)
    return {
        "ok": True,
        "mfa_required": False,
        "session_path": str(_session_path(cfg)),
        "message": "Logged in to Monarch and saved a local session.",
    }


def mfa_login(cfg, payload: dict[str, Any]) -> dict[str, Any]:
    code = str((payload or {}).get("code") or "").strip()
    if not code:
        raise RuntimeError("MFA code required")
    email, password = _credentials(cfg)
    mm = _client(cfg)
    mm.mfa_login(email=email, password=password, code=code)
    _secure_session_file(cfg)
    return {
        "ok": True,
        "session_path": str(_session_path(cfg)),
        "message": "MFA accepted. Monarch session saved.",
    }


def login_status(cfg) -> dict[str, Any]:
    path = _session_path(cfg)
    if not path.exists():
        return {"ok": True, "logged_in": False, "session_path": str(path), "message": "No Monarch session saved yet."}
    try:
        mm = _client(cfg)
        mm.load_session()
        accounts = mm.get_accounts()
        count = len(accounts.get("accounts") or [])
        return {
            "ok": True,
            "logged_in": True,
            "account_count": count,
            "session_path": str(path),
            "message": f"Logged in; {count} Monarch account(s) visible.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "logged_in": False,
            "session_path": str(path),
            "message": f"Saved session exists but failed verification: {exc}",
        }


def _account_group(account: dict[str, Any]) -> str:
    typ = (account.get("type_name") or "").lower()
    subtype = (account.get("subtype_name") or "").lower()
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


def _exports_path(cfg) -> Path:
    path = cfg.trackers_dir / "monarch" / "account_exports.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_exports_file(cfg) -> dict[str, Any]:
    path = _exports_path(cfg)
    if not path.exists():
        return {"accounts": {}}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {"accounts": {}}


def _write_exports_file(cfg, data: dict[str, Any]) -> None:
    path = _exports_path(cfg)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=False))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _db_accounts(cfg) -> list[dict[str, Any]]:
    con = connect(cfg.db_path, read_only=True)
    try:
        cur = con.execute(
            """
            SELECT account_id, display_name, mask, type_name, type_display, subtype_name,
                   subtype_display, institution_name, current_balance, display_balance,
                   include_in_net_worth, is_hidden, sync_disabled
            FROM monarch_accounts
            ORDER BY institution_name, display_name
            """
        )
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()


def _seed_exports(cfg, accounts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    data = _read_exports_file(cfg)
    exports = data.get("accounts")
    if not isinstance(exports, dict):
        exports = {}
    changed = False
    for account in accounts:
        account_id = account.get("account_id")
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
    if changed or not _exports_path(cfg).exists():
        data["notes"] = data.get("notes") or [
            "Enable only Monarch accounts that should appear in combined finance views.",
            "owner self: included in net worth.",
            "owner parents: excluded from net worth; positive outflows count as parent draw.",
        ]
        data["accounts"] = exports
        _write_exports_file(cfg, data)
    return exports


def _persist_account_settings(cfg, exports: dict[str, dict[str, Any]]) -> None:
    now = _now_iso()
    con = connect(cfg.db_path)
    try:
        label_rows = []
        export_rows = []
        for account_id, row in exports.items():
            owner = str(row.get("owner") or "self").strip() or "self"
            include_in_net_worth, parent_draw_source = _owner_flags(owner)
            group = str(row.get("account_group") or "other").strip()
            if group not in ACCOUNT_GROUPS:
                group = "other"
            label_rows.append(
                (
                    account_id,
                    str(row.get("label") or "").strip() or None,
                    owner,
                    group,
                    1 if include_in_net_worth else 0,
                    1 if parent_draw_source else 0,
                    now,
                )
            )
            export_rows.append((account_id, 1 if row.get("export_enabled") else 0, now))
        con.executemany(
            """
            INSERT INTO monarch_account_labels(
              account_id, label, owner, account_group, include_in_net_worth,
              parent_draw_source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
              label=excluded.label,
              owner=excluded.owner,
              account_group=excluded.account_group,
              include_in_net_worth=excluded.include_in_net_worth,
              parent_draw_source=excluded.parent_draw_source,
              updated_at=excluded.updated_at
            """,
            label_rows,
        )
        con.executemany(
            """
            INSERT INTO monarch_account_exports(account_id, export_enabled, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
              export_enabled=excluded.export_enabled,
              updated_at=excluded.updated_at
            """,
            export_rows,
        )
        con.commit()
    finally:
        con.close()


def accounts_status(cfg) -> dict[str, Any]:
    accounts = _db_accounts(cfg)
    exports = _seed_exports(cfg, accounts)
    rows = []
    for account in accounts:
        export = exports.get(account["account_id"]) or {}
        rows.append(
            {
                **account,
                "export_enabled": bool(export.get("export_enabled")),
                "label": export.get("label") or "",
                "owner": export.get("owner") or "self",
                "account_group": export.get("account_group") or _account_group(account),
                "include_in_net_worth": _owner_flags(export.get("owner") or "self")[0],
                "parent_draw_source": _owner_flags(export.get("owner") or "self")[1],
            }
        )
    return {
        "ok": True,
        "exports_path": str(_exports_path(cfg)),
        "account_count": len(rows),
        "accounts": rows,
    }


def save_account_exports(cfg, payload: dict[str, Any]) -> dict[str, Any]:
    db_accounts = {a["account_id"]: a for a in _db_accounts(cfg)}
    submitted = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(submitted, list):
        raise RuntimeError("Expected payload.accounts to be a list")
    exports = _seed_exports(cfg, list(db_accounts.values()))
    updated = 0
    for row in submitted:
        if not isinstance(row, dict):
            continue
        account_id = row.get("account_id")
        if account_id not in db_accounts:
            continue
        group = str(row.get("account_group") or exports.get(account_id, {}).get("account_group") or _account_group(db_accounts[account_id])).strip()
        if group not in ACCOUNT_GROUPS:
            raise RuntimeError(f"Invalid account_group for {account_id}: {group}")
        owner = str(row.get("owner") or "self").strip() or "self"
        include_in_net_worth, parent_draw_source = _owner_flags(owner)
        exports[account_id] = {
            "export_enabled": bool(row.get("export_enabled")),
            "label": str(row.get("label") or exports.get(account_id, {}).get("label") or "").strip(),
            "owner": owner,
            "account_group": group,
            "include_in_net_worth": include_in_net_worth,
            "parent_draw_source": parent_draw_source,
        }
        updated += 1
    data = _read_exports_file(cfg)
    data["notes"] = data.get("notes") or [
        "Enable only Monarch accounts that should appear in combined finance views.",
        "owner self: included in net worth.",
        "owner parents: excluded from net worth; positive outflows count as parent draw.",
    ]
    data["accounts"] = exports
    _write_exports_file(cfg, data)
    _persist_account_settings(cfg, exports)
    return {
        "ok": True,
        "updated_count": updated,
        "exports_path": str(_exports_path(cfg)),
        "message": f"Saved Monarch export settings for {updated} account(s). Run sync monarch to materialize them.",
    }


def debug_library(cfg) -> dict[str, Any]:
    """Small setup diagnostic proving the vendored Monarch client imports."""
    return {
        "ok": True,
        "session_path": str(_session_path(cfg)),
        "client": str(MonarchClient),
        "message": "Vendored Monarch read-only client import and construction succeeded.",
    }
