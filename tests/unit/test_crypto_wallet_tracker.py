import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import yaml
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.services.daemon.http import build_app
from personal_db.core.db import apply_tracker_schema, connect, init_db
from personal_db.core.installer import install_template
from personal_db.core.tracker import Tracker

ROOT = Path(__file__).resolve().parents[2]
CRYPTO_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "crypto_wallet"
FINANCE_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "finance"


def _load_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, CRYPTO_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_response(body, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def test_crypto_wallet_manifest_loads():
    from personal_db.core.manifest import load_manifest

    manifest = load_manifest(CRYPTO_DIR / "manifest.yaml")
    assert manifest.name == "crypto_wallet"
    assert manifest.permission_type == "api_key"
    assert "crypto_wallet_wallets" in manifest.schema.tables
    assert "crypto_wallet_token_balances" in manifest.schema.tables


def test_add_wallet_validates_with_moralis_and_writes_private_config(tmp_root, monkeypatch):
    actions = _load_module("actions.py", "crypto_wallet_actions_add_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    install_template(cfg, "crypto_wallet")
    monkeypatch.setenv("MORALIS_API_KEY", "test-key")

    seen = {}

    def fake_get(url, headers, params, timeout):
        seen["url"] = url
        seen["headers"] = headers
        seen["params"] = params
        return _fake_response(
            {
                "total_networth_usd": "123.45",
                "chains": [
                    {
                        "chain": "eth",
                        "native_balance_usd": "100.00",
                        "token_balance_usd": "23.45",
                    }
                ],
                "unsupported_chain_ids": [],
                "unavailable_chains": [],
            }
        )

    monkeypatch.setattr(actions.requests, "get", fake_get)
    result = actions.add_wallet(
        cfg,
        {
            "address": "0x1111111111111111111111111111111111111111",
            "label": "Main wallet",
            "chains": "eth,base",
        },
    )

    assert result["ok"] is True
    assert seen["headers"]["X-API-Key"] == "test-key"
    assert seen["params"]["chains"] == ["eth", "base"]
    path = cfg.trackers_dir / "crypto_wallet" / "wallets.yaml"
    assert path.stat().st_mode & 0o777 == 0o600
    data = yaml.safe_load(path.read_text())
    wallet = data["wallets"]["0x1111111111111111111111111111111111111111"]
    assert wallet["label"] == "Main wallet"
    assert wallet["wallet_type"] == "evm"
    assert wallet["validation_status"] == "valid"
    assert wallet["total_networth_usd"] == 123.45

    con = connect(cfg.db_path, read_only=True)
    row = con.execute(
        "SELECT label, total_networth_usd, validation_status FROM crypto_wallet_wallets"
    ).fetchone()
    con.close()
    assert row == ("Main wallet", 123.45, "valid")


def test_add_bitcoin_wallet_validates_with_universal_moralis(tmp_root, monkeypatch):
    actions = _load_module("actions.py", "crypto_wallet_actions_btc_add_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    install_template(cfg, "crypto_wallet")
    monkeypatch.setenv("MORALIS_API_KEY", "test-key")

    seen = {}

    def fake_get(url, headers, params, timeout):
        seen["url"] = url
        seen["headers"] = headers
        seen["params"] = params
        return _fake_response(
            {
                "result": [
                    {
                        "name": "Bitcoin",
                        "symbol": "BTC",
                        "decimals": 8,
                        "balance_formatted": "0.5",
                        "usd_price": "60000",
                        "usd_value": 30000,
                        "native_token": True,
                    }
                ]
            }
        )

    monkeypatch.setattr(actions.requests, "get", fake_get)
    address = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    result = actions.add_wallet(
        cfg,
        {
            "address": address,
            "wallet_type": "bitcoin",
            "label": "Cold BTC",
            "chains": "eth,base",
        },
    )

    assert result["ok"] is True
    assert seen["url"] == f"https://api.moralis.com/v1/wallets/{address}/tokens"
    assert seen["params"] == {"chains": "bitcoin"}
    data = yaml.safe_load((cfg.trackers_dir / "crypto_wallet" / "wallets.yaml").read_text())
    wallet = data["wallets"][f"bitcoin:{address}"]
    assert wallet["wallet_type"] == "bitcoin"
    assert wallet["chains"] == ["bitcoin"]
    assert wallet["total_networth_usd"] == 30000.0


def test_setup_page_renders_crypto_wallet_manager(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    install_template(cfg, "crypto_wallet")
    apply_tracker_schema(cfg.db_path, (CRYPTO_DIR / "schema.sql").read_text())
    client = TestClient(build_app(cfg))

    response = client.get("/setup/crypto_wallet")

    assert response.status_code == 200
    assert "crypto-wallet-manager" in response.text
    assert "Add &amp; validate" in response.text
    assert "Bitcoin" in response.text
    assert "deleteCryptoWallet" in response.text


def test_quantity_uses_decimals_for_raw_balance():
    ingest = _load_module("ingest.py", "crypto_wallet_ingest_quantity_test")

    quantity = ingest._quantity({"balance": "23450000", "decimals": 6})

    assert quantity == 23.45


def test_crypto_wallet_sync_exports_holdings_to_finance_contract(tmp_root, monkeypatch):
    ingest = _load_module("ingest.py", "crypto_wallet_ingest_sync_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    install_template(cfg, "crypto_wallet")
    monkeypatch.setenv("MORALIS_API_KEY", "test-key")
    apply_tracker_schema(cfg.db_path, (CRYPTO_DIR / "schema.sql").read_text())
    apply_tracker_schema(cfg.db_path, (FINANCE_DIR / "schema.sql").read_text())
    wallets_path = cfg.trackers_dir / "crypto_wallet" / "wallets.yaml"
    wallets_path.write_text(
        yaml.safe_dump(
            {
                "wallets": {
                    "0x1111111111111111111111111111111111111111": {
                        "address": "0x1111111111111111111111111111111111111111",
                        "label": "Main wallet",
                        "chains": ["eth"],
                        "owner": "self",
                        "account_group": "investments",
                        "export_enabled": True,
                    }
                }
            }
        )
    )

    def fake_get(url, headers, params, timeout):
        if url.endswith("/net-worth"):
            return _fake_response(
                {
                    "total_networth_usd": "123.45",
                    "chains": [
                        {
                            "chain": "eth",
                            "native_balance_usd": "100.00",
                            "token_balance_usd": "23.45",
                        }
                    ],
                }
            )
        if url.endswith("/tokens"):
            return _fake_response(
                {
                    "result": [
                        {
                            "name": "Ether",
                            "symbol": "ETH",
                            "decimals": 18,
                            "balance": "1000000000000000000",
                            "balance_formatted": "1",
                            "usd_price": "100",
                            "usd_value": 100,
                            "native_token": True,
                            "token_address": None,
                        },
                        {
                            "name": "USD Coin",
                            "symbol": "USDC",
                            "decimals": 6,
                            "balance": "23450000",
                            "balance_formatted": "23.45",
                            "usd_price": "1",
                            "usd_value": 23.45,
                            "native_token": False,
                            "token_address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                            "verified_contract": True,
                        },
                    ],
                    "block_number": "123",
                    "cursor": None,
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(ingest.requests, "get", fake_get)
    ingest.sync(Tracker("crypto_wallet", cfg, manifest=None))

    con = connect(cfg.db_path, read_only=True)
    account = con.execute(
        """
        SELECT source, source_account_id, account_group, current_balance
        FROM crypto_wallet_finance_accounts_export
        """
    ).fetchone()
    holdings = con.execute(
        """
        SELECT ticker, quantity, price, value
        FROM crypto_wallet_finance_holdings_export
        ORDER BY ticker
        """
    ).fetchall()
    con.close()

    assert account == (
        "crypto_wallet",
        "0x1111111111111111111111111111111111111111",
        "investments",
        123.45,
    )
    assert holdings == [("ETH", 1.0, 100.0, 100.0), ("USDC", 23.45, 1.0, 23.45)]


def test_crypto_wallet_sync_exports_bitcoin_holding(tmp_root, monkeypatch):
    ingest = _load_module("ingest.py", "crypto_wallet_ingest_btc_sync_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    install_template(cfg, "crypto_wallet")
    monkeypatch.setenv("MORALIS_API_KEY", "test-key")
    apply_tracker_schema(cfg.db_path, (CRYPTO_DIR / "schema.sql").read_text())
    apply_tracker_schema(cfg.db_path, (FINANCE_DIR / "schema.sql").read_text())
    address = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    wallets_path = cfg.trackers_dir / "crypto_wallet" / "wallets.yaml"
    wallets_path.write_text(
        yaml.safe_dump(
            {
                "wallets": {
                    f"bitcoin:{address}": {
                        "address": address,
                        "wallet_type": "bitcoin",
                        "label": "Cold BTC",
                        "chains": ["bitcoin"],
                        "owner": "self",
                        "account_group": "investments",
                        "export_enabled": True,
                    }
                }
            }
        )
    )

    def fake_get(url, headers, params, timeout):
        assert url == f"https://api.moralis.com/v1/wallets/{address}/tokens"
        assert params == {"chains": "bitcoin"}
        return _fake_response(
            {
                "result": [
                    {
                        "name": "Bitcoin",
                        "symbol": "BTC",
                        "decimals": 8,
                        "balance": "50000000",
                        "usd_price": "60000",
                        "usd_value": 30000,
                    }
                ]
            }
        )

    monkeypatch.setattr(ingest.requests, "get", fake_get)
    ingest.sync(Tracker("crypto_wallet", cfg, manifest=None))

    con = connect(cfg.db_path, read_only=True)
    account = con.execute(
        """
        SELECT source_account_id, type, subtype, current_balance
        FROM crypto_wallet_finance_accounts_export
        """
    ).fetchone()
    holding = con.execute(
        """
        SELECT ticker, type, quantity, price, value
        FROM crypto_wallet_finance_holdings_export
        """
    ).fetchone()
    con.close()

    assert account == (f"bitcoin:{address}", "bitcoin_wallet", "bitcoin", 30000.0)
    assert holding == ("BTC", "native_crypto", 0.5, 60000.0, 30000.0)
