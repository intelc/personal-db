"""Moralis-backed crypto wallet ingest."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import requests
import yaml

from personal_db.db import connect
from personal_db.tracker import Tracker

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


def _api_key(t: Tracker) -> str:
    key = _env(t, "MORALIS_API_KEY")
    if not key:
        raise RuntimeError("Set MORALIS_API_KEY env var (see manifest setup_steps)")
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


def _parse_chains(t: Tracker, value: Any, wallet_type: str = "evm") -> list[str]:
    if wallet_type == "bitcoin":
        return ["bitcoin"]
    default_raw = _env(t, "MORALIS_DEFAULT_CHAINS")
    default = [part.strip() for part in (default_raw or "").split(",") if part.strip()] or DEFAULT_CHAINS
    if value is None:
        return default
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(part).strip() for part in parsed if str(part).strip()] or default
        except json.JSONDecodeError:
            pass
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


def _wallets_path(t: Tracker) -> Path:
    path = t.cfg.trackers_dir / "crypto_wallet" / "wallets.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_wallets(t: Tracker) -> dict[str, Any]:
    path = _wallets_path(t)
    if not path.exists():
        return {"wallets": {}}
    data = yaml.safe_load(path.read_text()) or {}
    return data if isinstance(data, dict) else {"wallets": {}}


def _write_wallets(t: Tracker, data: dict[str, Any]) -> None:
    path = _wallets_path(t)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=False))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _wallet_entries(t: Tracker) -> dict[str, dict[str, Any]]:
    data = _read_wallets(t)
    wallets = data.get("wallets")
    return wallets if isinstance(wallets, dict) else {}


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quantity(row: dict[str, Any]) -> float | None:
    formatted = row.get("balance_formatted") or row.get("balanceFormatted")
    if formatted not in (None, ""):
        return _coerce_float(formatted)
    raw = row.get("balance") or row.get("balanceRaw")
    decimals = row.get("decimals")
    try:
        return float(Decimal(str(raw)) / (Decimal(10) ** Decimal(int(decimals or 0))))
    except (InvalidOperation, TypeError, ValueError, ArithmeticError):
        return None


def _moralis_get(t: Tracker, base_url: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = requests.get(
        f"{base_url}{path}",
        headers={"X-API-Key": _api_key(t)},
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


def _net_worth(t: Tracker, address: str, chains: list[str]) -> dict[str, Any]:
    return _moralis_get(
        t,
        EVM_MORALIS_BASE_URL,
        f"/wallets/{address}/net-worth",
        {
            "chains": chains,
            "exclude_spam": "true",
            "exclude_unverified_contracts": "false",
        },
    )


def _bitcoin_tokens(t: Tracker, address: str) -> dict[str, Any]:
    return _moralis_get(
        t,
        UNIVERSAL_MORALIS_BASE_URL,
        f"/wallets/{address}/tokens",
        {"chains": "bitcoin"},
    )


def _token_balances(t: Tracker, address: str, chain: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    out = []
    cursor = None
    while True:
        params = {
            "chain": chain,
            "exclude_spam": "true",
            "exclude_unverified_contracts": "false",
            "limit": 100,
            "exclude_native": "false",
        }
        if cursor:
            params["cursor"] = cursor
        body = _moralis_get(t, EVM_MORALIS_BASE_URL, f"/wallets/{address}/tokens", params)
        for row in body.get("result") or []:
            if isinstance(row, dict):
                out.append((body, row))
        cursor = body.get("cursor")
        if not cursor:
            break
    return out


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


def _wallet_row(
    t: Tracker,
    wallet_id: str,
    item: dict[str, Any],
    wallet_type: str,
    net_worth: dict[str, Any],
    holdings_value: float,
    now: str,
) -> dict[str, Any]:
    owner = str(item.get("owner") or "self").strip() or "self"
    include_in_net_worth, parent_draw_source = _owner_flags(owner)
    group = str(item.get("account_group") or "investments").strip()
    if group not in ACCOUNT_GROUPS:
        group = "investments"
    if wallet_type == "bitcoin":
        native_usd = holdings_value
        token_usd = 0.0
        total_networth = holdings_value
    else:
        native_usd, token_usd = _chain_totals(net_worth)
        total_networth = _coerce_float(net_worth.get("total_networth_usd"))
    return {
        "wallet_id": wallet_id,
        "address": item.get("address") or wallet_id,
        "label": str(item.get("label") or "").strip() or None,
        "chains": json.dumps(_parse_chains(t, item.get("chains"), wallet_type), separators=(",", ":")),
        "owner": owner,
        "account_group": group,
        "export_enabled": 1 if item.get("export_enabled", True) else 0,
        "include_in_net_worth": 1 if include_in_net_worth else 0,
        "parent_draw_source": 1 if parent_draw_source else 0,
        "total_networth_usd": total_networth,
        "native_balance_usd": native_usd,
        "token_balance_usd": token_usd,
        "holdings_value_usd": holdings_value,
        "validation_status": "valid",
        "validation_error": None,
        "last_validated_at": now,
        "updated_at": now,
        "raw_json": _json(net_worth),
    }


def _holding_row(wallet_id: str, address: str, chain: str, body: dict[str, Any], row: dict[str, Any], now: str) -> dict[str, Any]:
    native = chain == "bitcoin" or bool(row.get("native_token") or row.get("nativeToken"))
    token_address = row.get("token_address") or row.get("tokenAddress")
    token_key = "native" if native and not token_address else str(token_address or "").lower()
    return {
        "holding_id": f"{wallet_id}:{chain}:{token_key}",
        "wallet_id": wallet_id,
        "address": address,
        "chain": chain,
        "block_number": body.get("block_number") or body.get("blockNumber"),
        "token_address": token_key,
        "native_token": 1 if native else 0,
        "name": row.get("name"),
        "symbol": row.get("symbol"),
        "decimals": row.get("decimals"),
        "balance_raw": row.get("balance") or row.get("balanceRaw"),
        "balance_formatted": row.get("balance_formatted") or row.get("balanceFormatted"),
        "quantity": _quantity(row),
        "usd_price": _coerce_float(row.get("usd_price") or row.get("usdPrice")),
        "usd_value": _token_usd_value(row),
        "possible_spam": 1 if row.get("possible_spam") or row.get("possibleSpam") else 0,
        "verified_contract": 1 if row.get("verified_contract") or row.get("verifiedContract") else 0,
        "logo": row.get("logo"),
        "thumbnail": row.get("thumbnail"),
        "fetched_at": now,
        "raw_json": _json(row),
    }


def _mark_invalid(t: Tracker, wallets: dict[str, dict[str, Any]], wallet_id: str, error: str, now: str) -> None:
    item = wallets[wallet_id]
    item["validation_status"] = "invalid"
    item["validation_error"] = error
    item["last_validated_at"] = now
    data = _read_wallets(t)
    data["wallets"] = wallets
    _write_wallets(t, data)


def _clear_stale_holdings(t: Tracker, wallet_ids: list[str]) -> None:
    con = connect(t.cfg.db_path)
    try:
        if not wallet_ids:
            con.execute("DELETE FROM crypto_wallet_token_balances")
            con.execute("DELETE FROM crypto_wallet_wallets")
        else:
            placeholders = ",".join("?" for _ in wallet_ids)
            con.execute(f"DELETE FROM crypto_wallet_token_balances WHERE wallet_id IN ({placeholders})", wallet_ids)
            con.execute(f"DELETE FROM crypto_wallet_wallets WHERE wallet_id NOT IN ({placeholders})", wallet_ids)
            con.execute(f"DELETE FROM crypto_wallet_token_balances WHERE wallet_id NOT IN ({placeholders})", wallet_ids)
        con.commit()
    finally:
        con.close()


def _token_balance_snapshot_row(row: dict[str, Any], now: str) -> dict[str, Any]:
    return {
        "snapshot_id": f"{now[:10]}:{row['holding_id']}",
        "date": now[:10],
        **row,
    }


def sync(t: Tracker) -> None:
    now = _now_iso()
    wallets = _wallet_entries(t)
    if not wallets:
        _clear_stale_holdings(t, [])
        t.cursor.set(now)
        t.log.info("crypto_wallet: no wallets configured")
        return

    wallet_rows = []
    holding_rows = []
    synced_wallet_ids = []
    for wallet_id, item in wallets.items():
        try:
            address = _wallet_input(item.get("address") or wallet_id)
            wallet_type = _wallet_type(address, item.get("wallet_type"))
            wallet_id = _wallet_id(address, wallet_type)
            chains = _parse_chains(t, item.get("chains"), wallet_type)
            wallet_holdings = []
            if wallet_type == "bitcoin":
                net_worth = _bitcoin_tokens(t, address)
                for token in _token_rows(net_worth):
                    wallet_holdings.append(_holding_row(wallet_id, address, "bitcoin", net_worth, token, now))
            else:
                net_worth = _net_worth(t, address, chains)
                for chain in chains:
                    try:
                        for body, token in _token_balances(t, address, chain):
                            wallet_holdings.append(_holding_row(wallet_id, address, chain, body, token, now))
                    except Exception as exc:
                        t.log.warning("crypto_wallet: token balances failed for %s on %s: %s", address, chain, exc)
            holdings_value = sum(_coerce_float(row.get("usd_value")) or 0.0 for row in wallet_holdings)
            wallet_rows.append(_wallet_row(t, wallet_id, item, wallet_type, net_worth, holdings_value, now))
            holding_rows.extend(wallet_holdings)
            synced_wallet_ids.append(wallet_id)
            item.update(
                {
                    "wallet_type": wallet_type,
                    "total_networth_usd": wallet_rows[-1]["total_networth_usd"],
                    "native_balance_usd": wallet_rows[-1]["native_balance_usd"],
                    "token_balance_usd": wallet_rows[-1]["token_balance_usd"],
                    "holdings_value_usd": holdings_value,
                    "validation_status": "valid",
                    "validation_error": None,
                    "last_validated_at": now,
                    "raw_json": wallet_rows[-1]["raw_json"],
                }
            )
        except Exception as exc:
            t.log.warning("crypto_wallet: wallet sync failed for %s: %s", wallet_id, exc)
            _mark_invalid(t, wallets, wallet_id, str(exc), now)

    data = _read_wallets(t)
    data["wallets"] = wallets
    _write_wallets(t, data)
    _clear_stale_holdings(t, synced_wallet_ids)
    t.upsert("crypto_wallet_wallets", wallet_rows, key=["wallet_id"])
    t.upsert("crypto_wallet_token_balances", holding_rows, key=["holding_id"])
    t.upsert(
        "crypto_wallet_token_balance_snapshots",
        [_token_balance_snapshot_row(row, now) for row in holding_rows],
        key=["snapshot_id"],
    )
    t.cursor.set(now)
    t.log.info("crypto_wallet: %d wallets, %d holdings", len(wallet_rows), len(holding_rows))
