"""User-triggered crypto wallet setup actions."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
import yaml

from personal_db.db import apply_tracker_schema, connect

ACCOUNT_GROUPS = {"cash", "credit_card", "investments", "other"}
DEFAULT_CHAINS = ["eth", "base", "arbitrum", "optimism", "polygon", "bsc"]
EVM_MORALIS_BASE_URL = "https://deep-index.moralis.io/api/v2.2"
UNIVERSAL_MORALIS_BASE_URL = "https://api.moralis.com/v1"
PARENT_OWNERS = {"parents"}
BTC_ADDRESS_RE = re.compile(r"^([13][a-km-zA-HJ-NP-Z1-9]{25,34}|(bc1|tb1)[a-zA-HJ-NP-Z0-9]{11,90})$")
EVM_WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
XPUB_RE = re.compile(r"^(xpub|ypub|zpub|tpub|upub|vpub)[A-Za-z0-9]{50,120}$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def _env(cfg, name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or _read_env_file(cfg.root).get(name) or default


def _api_key(cfg) -> str:
    key = _env(cfg, "MORALIS_API_KEY")
    if not key:
        raise RuntimeError("Set MORALIS_API_KEY first")
    return key


def _wallet_input(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("bitcoin:"):
        return text.split(":", 1)[1]
    return text


def _wallet_type(address: str, explicit: str | None = None) -> str:
    requested = str(explicit or "auto").strip().lower()
    address = _wallet_input(address)
    if requested == "evm":
        if not EVM_WALLET_RE.match(address):
            raise ValueError("EVM wallet address must be 0x followed by 40 hex characters")
        return "evm"
    if requested == "bitcoin":
        if not (BTC_ADDRESS_RE.match(address) or XPUB_RE.match(address)):
            raise ValueError("Bitcoin wallet must be a Bitcoin address or xpub/ypub/zpub")
        return "bitcoin"
    if EVM_WALLET_RE.match(address):
        return "evm"
    if BTC_ADDRESS_RE.match(address) or XPUB_RE.match(address):
        return "bitcoin"
    raise ValueError("Wallet must be an EVM 0x address, Bitcoin address, or Bitcoin xpub/ypub/zpub")


def _parse_chains(value: Any, cfg=None, wallet_type: str = "evm") -> list[str]:
    if wallet_type == "bitcoin":
        return ["bitcoin"]
    default_raw = _env(cfg, "MORALIS_DEFAULT_CHAINS") if cfg is not None else None
    default = [part.strip() for part in (default_raw or "").split(",") if part.strip()] or DEFAULT_CHAINS
    if value is None:
        return default
    if isinstance(value, str):
        chains = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        chains = [str(part).strip() for part in value if str(part).strip()]
    else:
        chains = []
    return chains or default


def _wallet_id(address: str, wallet_type: str | None = None) -> str:
    address = _wallet_input(address)
    kind = _wallet_type(address, wallet_type)
    if kind == "evm":
        return address.lower()
    return f"bitcoin:{address.lower()}"


def _owner_flags(owner: str) -> tuple[bool, bool]:
    is_parent = owner.strip().lower() in PARENT_OWNERS
    return (not is_parent, is_parent)


def _wallets_path(cfg) -> Path:
    path = cfg.trackers_dir / "crypto_wallet" / "wallets.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_schema(cfg) -> None:
    schema_path = cfg.trackers_dir / "crypto_wallet" / "schema.sql"
    if schema_path.exists():
        apply_tracker_schema(cfg.db_path, schema_path.read_text())


def _read_wallets_file(cfg) -> dict[str, Any]:
    path = _wallets_path(cfg)
    if not path.exists():
        return {"wallets": {}}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {"wallets": {}}


def _write_wallets_file(cfg, data: dict[str, Any]) -> None:
    path = _wallets_path(cfg)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=False))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _wallet_entries(cfg) -> dict[str, dict[str, Any]]:
    data = _read_wallets_file(cfg)
    wallets = data.get("wallets")
    return wallets if isinstance(wallets, dict) else {}


def _moralis_get(cfg, base_url: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = requests.get(
        f"{base_url}{path}",
        headers={"X-API-Key": _api_key(cfg)},
        params=params or {},
        timeout=30,
    )
    if r.status_code >= 400:
        try:
            body = r.json()
            message = body.get("message") or body.get("error") or str(body)
        except ValueError:
            message = r.text or f"HTTP {r.status_code}"
        raise RuntimeError(message)
    return r.json()


def _net_worth(cfg, address: str, chains: list[str]) -> dict[str, Any]:
    return _moralis_get(
        cfg,
        EVM_MORALIS_BASE_URL,
        f"/wallets/{address}/net-worth",
        {
            "chains": chains,
            "exclude_spam": "true",
            "exclude_unverified_contracts": "false",
        },
    )


def _bitcoin_tokens(cfg, address: str) -> dict[str, Any]:
    return _moralis_get(
        cfg,
        UNIVERSAL_MORALIS_BASE_URL,
        f"/wallets/{address}/tokens",
        {"chains": "bitcoin"},
    )


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _chain_totals(body: dict[str, Any]) -> tuple[float, float]:
    native = 0.0
    token = 0.0
    for row in body.get("chains") or []:
        if not isinstance(row, dict):
            continue
        native += _coerce_float(row.get("native_balance_usd")) or 0.0
        token += _coerce_float(row.get("token_balance_usd")) or 0.0
    return native, token


def _token_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    rows = body.get("result") or body.get("tokens") or body.get("data") or []
    return [row for row in rows if isinstance(row, dict)]


def _token_usd_value(row: dict[str, Any]) -> float:
    return (
        _coerce_float(row.get("usd_value"))
        or _coerce_float(row.get("usdValue"))
        or _coerce_float(row.get("value_usd"))
        or _coerce_float(row.get("valueUsd"))
        or 0.0
    )


def _validate_payload(cfg, payload: dict[str, Any]) -> dict[str, Any]:
    address = _wallet_input(payload.get("address"))
    wallet_type = _wallet_type(address, payload.get("wallet_type"))
    wallet_id = _wallet_id(address, wallet_type)
    chains = _parse_chains(payload.get("chains"), cfg, wallet_type)
    if wallet_type == "bitcoin":
        body = _bitcoin_tokens(cfg, address)
        native_usd = sum(_token_usd_value(row) for row in _token_rows(body))
        token_usd = 0.0
        total_networth_usd = native_usd
    else:
        body = _net_worth(cfg, address, chains)
        native_usd, token_usd = _chain_totals(body)
        total_networth_usd = _coerce_float(body.get("total_networth_usd"))
    now = _now_iso()
    return {
        "wallet_id": wallet_id,
        "address": address,
        "wallet_type": wallet_type,
        "chains": chains,
        "total_networth_usd": total_networth_usd,
        "native_balance_usd": native_usd,
        "token_balance_usd": token_usd,
        "validation_status": "valid",
        "validation_error": None,
        "last_validated_at": now,
        "raw_json": _compact_json(body),
        "unsupported_chain_ids": body.get("unsupported_chain_ids") or [],
        "unavailable_chains": body.get("unavailable_chains") or [],
    }


def _materialize_wallets(cfg, wallets: dict[str, dict[str, Any]]) -> None:
    _ensure_schema(cfg)
    now = _now_iso()
    rows = []
    for wallet_id, item in wallets.items():
        wallet_type = str(item.get("wallet_type") or _wallet_type(item.get("address") or wallet_id)).strip().lower()
        owner = str(item.get("owner") or "self").strip() or "self"
        include_in_net_worth, parent_draw_source = _owner_flags(owner)
        group = str(item.get("account_group") or "investments").strip()
        if group not in ACCOUNT_GROUPS:
            group = "investments"
        rows.append(
            (
                wallet_id,
                item.get("address") or wallet_id,
                str(item.get("label") or "").strip() or None,
                json.dumps(_parse_chains(item.get("chains"), cfg, wallet_type), separators=(",", ":")),
                owner,
                group,
                1 if item.get("export_enabled", True) else 0,
                1 if include_in_net_worth else 0,
                1 if parent_draw_source else 0,
                item.get("total_networth_usd"),
                item.get("native_balance_usd"),
                item.get("token_balance_usd"),
                item.get("holdings_value_usd"),
                item.get("validation_status") or "unvalidated",
                item.get("validation_error"),
                item.get("last_validated_at"),
                now,
                item.get("raw_json"),
            )
        )
    con = connect(cfg.db_path)
    try:
        wallet_ids = list(wallets.keys())
        if wallet_ids:
            placeholders = ",".join("?" for _ in wallet_ids)
            con.execute(
                f"DELETE FROM crypto_wallet_wallets WHERE wallet_id NOT IN ({placeholders})",
                wallet_ids,
            )
            con.execute(
                f"DELETE FROM crypto_wallet_token_balances WHERE wallet_id NOT IN ({placeholders})",
                wallet_ids,
            )
        else:
            con.execute("DELETE FROM crypto_wallet_wallets")
            con.execute("DELETE FROM crypto_wallet_token_balances")
        con.executemany(
            """
            INSERT INTO crypto_wallet_wallets(
              wallet_id, address, label, chains, owner, account_group, export_enabled,
              include_in_net_worth, parent_draw_source, total_networth_usd,
              native_balance_usd, token_balance_usd, holdings_value_usd,
              validation_status, validation_error, last_validated_at, updated_at, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet_id) DO UPDATE SET
              address=excluded.address,
              label=excluded.label,
              chains=excluded.chains,
              owner=excluded.owner,
              account_group=excluded.account_group,
              export_enabled=excluded.export_enabled,
              include_in_net_worth=excluded.include_in_net_worth,
              parent_draw_source=excluded.parent_draw_source,
              total_networth_usd=excluded.total_networth_usd,
              native_balance_usd=excluded.native_balance_usd,
              token_balance_usd=excluded.token_balance_usd,
              holdings_value_usd=excluded.holdings_value_usd,
              validation_status=excluded.validation_status,
              validation_error=excluded.validation_error,
              last_validated_at=excluded.last_validated_at,
              updated_at=excluded.updated_at,
              raw_json=excluded.raw_json
            """,
            rows,
        )
        con.commit()
    finally:
        con.close()


def wallets_status(cfg) -> dict[str, Any]:
    wallets = _wallet_entries(cfg)
    _materialize_wallets(cfg, wallets)
    rows = []
    for wallet_id, item in sorted(wallets.items()):
        wallet_type = item.get("wallet_type") or _wallet_type(item.get("address") or wallet_id)
        rows.append(
            {
                "wallet_id": wallet_id,
                "address": item.get("address") or wallet_id,
                "wallet_type": wallet_type,
                "label": item.get("label") or "",
                "chains": _parse_chains(item.get("chains"), cfg, wallet_type),
                "owner": item.get("owner") or "self",
                "account_group": item.get("account_group") or "investments",
                "export_enabled": bool(item.get("export_enabled", True)),
                "total_networth_usd": item.get("total_networth_usd"),
                "holdings_value_usd": item.get("holdings_value_usd"),
                "validation_status": item.get("validation_status") or "unvalidated",
                "validation_error": item.get("validation_error"),
                "last_validated_at": item.get("last_validated_at"),
            }
        )
    return {
        "ok": True,
        "wallets_path": str(_wallets_path(cfg)),
        "wallet_count": len(rows),
        "wallets": rows,
        "message": f"{len(rows)} configured wallet(s).",
    }


def add_wallet(cfg, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        validated = _validate_payload(cfg, payload or {})
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    wallets = _wallet_entries(cfg)
    owner = str(payload.get("owner") or "self").strip() or "self"
    include_in_net_worth, parent_draw_source = _owner_flags(owner)
    group = str(payload.get("account_group") or "investments").strip()
    if group not in ACCOUNT_GROUPS:
        group = "investments"
    wallets[validated["wallet_id"]] = {
        "address": validated["address"],
        "wallet_type": validated["wallet_type"],
        "label": str(payload.get("label") or "").strip(),
        "chains": validated["chains"],
        "owner": owner,
        "account_group": group,
        "export_enabled": bool(payload.get("export_enabled", True)),
        "include_in_net_worth": include_in_net_worth,
        "parent_draw_source": parent_draw_source,
        "total_networth_usd": validated["total_networth_usd"],
        "native_balance_usd": validated["native_balance_usd"],
        "token_balance_usd": validated["token_balance_usd"],
        "validation_status": "valid",
        "validation_error": None,
        "last_validated_at": validated["last_validated_at"],
        "raw_json": validated["raw_json"],
    }
    data = _read_wallets_file(cfg)
    data["wallets"] = wallets
    _write_wallets_file(cfg, data)
    _materialize_wallets(cfg, wallets)
    return {
        "ok": True,
        "wallet": wallets[validated["wallet_id"]],
        "unsupported_chain_ids": validated["unsupported_chain_ids"],
        "unavailable_chains": validated["unavailable_chains"],
        "message": f"Added and validated {validated['wallet_type']} wallet {validated['address']}.",
    }


def save_wallets(cfg, payload: dict[str, Any]) -> dict[str, Any]:
    submitted = payload.get("wallets") if isinstance(payload, dict) else None
    if not isinstance(submitted, list):
        raise RuntimeError("Expected payload.wallets to be a list")
    existing = _wallet_entries(cfg)
    wallets: dict[str, dict[str, Any]] = {}
    for row in submitted:
        if not isinstance(row, dict):
            continue
        address = _wallet_input(row.get("address") or row.get("wallet_id") or "")
        wallet_type = _wallet_type(address, row.get("wallet_type"))
        wallet_id = _wallet_id(address, wallet_type)
        previous = existing.get(wallet_id) or {}
        owner = str(row.get("owner") or previous.get("owner") or "self").strip() or "self"
        include_in_net_worth, parent_draw_source = _owner_flags(owner)
        group = str(row.get("account_group") or previous.get("account_group") or "investments").strip()
        if group not in ACCOUNT_GROUPS:
            raise RuntimeError(f"Invalid account_group for {wallet_id}: {group}")
        wallets[wallet_id] = {
            **previous,
            "address": address,
            "wallet_type": wallet_type,
            "label": str(row.get("label") or "").strip(),
            "chains": _parse_chains(row.get("chains"), cfg, wallet_type),
            "owner": owner,
            "account_group": group,
            "export_enabled": bool(row.get("export_enabled", True)),
            "include_in_net_worth": include_in_net_worth,
            "parent_draw_source": parent_draw_source,
        }
    data = _read_wallets_file(cfg)
    data["wallets"] = wallets
    _write_wallets_file(cfg, data)
    _materialize_wallets(cfg, wallets)
    return {
        "ok": True,
        "wallet_count": len(wallets),
        "wallets_path": str(_wallets_path(cfg)),
        "message": f"Saved {len(wallets)} wallet(s).",
    }


def validate_wallet(cfg, payload: dict[str, Any]) -> dict[str, Any]:
    address = _wallet_input((payload or {}).get("address"))
    try:
        wallet_type = _wallet_type(address, (payload or {}).get("wallet_type"))
        wallet_id = _wallet_id(address, wallet_type)
        wallets = _wallet_entries(cfg)
        existing = wallets.get(wallet_id) or {}
        validated = _validate_payload(
            cfg,
            {
                "address": address,
                "wallet_type": wallet_type,
                "chains": (payload or {}).get("chains") or existing.get("chains"),
            },
        )
        if existing:
            existing.update(
                {
                    "chains": validated["chains"],
                    "wallet_type": validated["wallet_type"],
                    "total_networth_usd": validated["total_networth_usd"],
                    "native_balance_usd": validated["native_balance_usd"],
                    "token_balance_usd": validated["token_balance_usd"],
                    "validation_status": "valid",
                    "validation_error": None,
                    "last_validated_at": validated["last_validated_at"],
                    "raw_json": validated["raw_json"],
                }
            )
            wallets[wallet_id] = existing
            data = _read_wallets_file(cfg)
            data["wallets"] = wallets
            _write_wallets_file(cfg, data)
            _materialize_wallets(cfg, wallets)
        return {"ok": True, **validated, "message": f"{validated['wallet_type']} wallet {address} validated."}
    except Exception as exc:
        return {"ok": False, "address": address, "message": str(exc)}
