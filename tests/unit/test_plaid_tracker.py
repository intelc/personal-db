import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, connect, init_db
from personal_db.core.tracker import Tracker

PLAID_DIR = Path(__file__).resolve().parents[2] / "src" / "personal_db" / "templates" / "trackers" / "plaid"


def _load_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, PLAID_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_plaid_manifest_loads():
    from personal_db.core.manifest import load_manifest

    manifest = load_manifest(PLAID_DIR / "manifest.yaml")
    assert manifest.name == "plaid"
    assert manifest.permission_type == "api_key"
    assert "plaid_transactions" in manifest.schema.tables
    assert "plaid_investment_holdings" in manifest.schema.tables


def test_link_token_omits_transactions_options_when_transactions_not_requested(tmp_root, monkeypatch):
    actions = _load_module("actions.py", "plaid_actions_test")
    cfg = Config(root=tmp_root)
    monkeypatch.setenv("PLAID_CLIENT_ID", "cid")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_PRODUCTS", "investments")
    monkeypatch.setenv("PLAID_OPTIONAL_PRODUCTS", "")

    seen = {}

    def fake_post(url, json, timeout):
        seen["url"] = url
        seen["json"] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"link_token": "link-sandbox-123"}
        return resp

    monkeypatch.setattr(actions.requests, "post", fake_post)
    assert actions._link_token(cfg) == "link-sandbox-123"
    assert seen["url"] == "https://development.plaid.com/link/token/create"
    assert seen["json"]["products"] == ["investments"]
    assert "transactions" not in seen["json"]


def test_link_token_includes_configured_redirect_uri(tmp_root, monkeypatch):
    actions = _load_module("actions.py", "plaid_actions_redirect_test")
    cfg = Config(root=tmp_root)
    monkeypatch.setenv("PLAID_CLIENT_ID", "cid")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_REDIRECT_URI", "https://localhost:9878/oauth-return")

    seen = {}

    def fake_post(url, json, timeout):
        seen["json"] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"link_token": "link-development-redirect"}
        return resp

    monkeypatch.setattr(actions.requests, "post", fake_post)
    assert actions._link_token(cfg) == "link-development-redirect"
    assert seen["json"]["redirect_uri"] == "https://localhost:9878/oauth-return"


def test_configured_redirect_requires_local_explicit_port(tmp_root, monkeypatch):
    actions = _load_module("actions.py", "plaid_actions_bad_redirect_test")
    cfg = Config(root=tmp_root)
    monkeypatch.setenv("PLAID_REDIRECT_URI", "https://example.com/oauth-return")

    import pytest

    with pytest.raises(RuntimeError, match="local"):
        actions._configured_redirect(cfg)


def test_configured_redirect_requires_https(tmp_root, monkeypatch):
    actions = _load_module("actions.py", "plaid_actions_http_redirect_test")
    cfg = Config(root=tmp_root)
    monkeypatch.setenv("PLAID_REDIRECT_URI", "http://localhost:9878/oauth-return")

    import pytest

    with pytest.raises(RuntimeError, match="https"):
        actions._configured_redirect(cfg)


def test_link_item_reuses_existing_helper_when_redirect_port_busy(tmp_root, monkeypatch):
    actions = _load_module("actions.py", "plaid_actions_busy_port_test")
    cfg = Config(root=tmp_root)
    monkeypatch.setenv("PLAID_REDIRECT_URI", "https://localhost:9878/oauth-return")
    monkeypatch.setattr(actions, "_find_port", lambda: (_ for _ in ()).throw(actions._PortInUse()))
    opened = []
    monkeypatch.setattr(actions.webbrowser, "open", lambda url: opened.append(url))

    result = actions.link_item(cfg)

    assert result["ok"] is True
    assert result["url"] == "https://localhost:9878/"
    assert "already running" in result["message"]
    assert opened == ["https://localhost:9878/"]


def test_port_accepts_connections_false_when_nothing_listens():
    actions = _load_module("actions.py", "plaid_actions_port_probe_test")

    assert actions._port_accepts_connections("127.0.0.1", 9) is False


def test_save_item_writes_private_state_file(tmp_root):
    actions = _load_module("actions.py", "plaid_actions_state_test")
    cfg = Config(root=tmp_root)

    actions._save_item(
        cfg,
        {
            "item_id": "item-1",
            "access_token": "access-development-1",
            "institution_name": "Chase",
            "created_at": "2026-05-30T12:00:00+00:00",
        },
    )

    path = tmp_root / "state" / "plaid" / "items.json"
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    data = json.loads(path.read_text())
    assert data["items"][0]["access_token"] == "access-development-1"
    backups = list((tmp_root / "state" / "plaid" / "backups").glob("items-*.json"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert json.loads(backups[0].read_text())["items"][0]["access_token"] == "access-development-1"


def test_backup_tokens_and_token_status_do_not_print_access_tokens(tmp_root):
    actions = _load_module("actions.py", "plaid_actions_backup_test")
    cfg = Config(root=tmp_root)
    actions._save_item(
        cfg,
        {
            "item_id": "item-1",
            "access_token": "access-development-secret",
            "institution_name": "Amex",
            "created_at": "2026-05-30T12:00:00+00:00",
        },
    )

    result = actions.backup_tokens(cfg)
    assert result["ok"] is True
    assert result["item_count"] == 1
    assert Path(result["backup_path"]).exists()

    status = actions.token_status(cfg)
    rendered = json.dumps(status)
    assert status["item_count"] == 1
    assert status["items"][0]["has_access_token"] is True
    assert "access-development-secret" not in rendered
    assert status["backup_count"] >= 2
    assert "recent_link_events" in status


def test_record_link_event_writes_private_diagnostics(tmp_root):
    actions = _load_module("actions.py", "plaid_actions_events_test")
    cfg = Config(root=tmp_root)
    actions._record_link_event(
        cfg,
        "exchange_failed",
        {
            "institution": {"institution_id": "ins_11", "name": "Charles Schwab"},
            "link_session_id": "session-1",
            "accounts": [{"id": "a"}],
        },
        "bad public token",
    )

    path = tmp_root / "state" / "plaid" / "link_events.json"
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    event = json.loads(path.read_text())["events"][0]
    assert event["institution_name"] == "Charles Schwab"
    assert event["event"] == "exchange_failed"
    assert event["error"] == "bad public token"


def test_account_label_actions_round_trip(tmp_root):
    actions = _load_module("actions.py", "plaid_actions_label_editor_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (PLAID_DIR / "schema.sql").read_text())
    con = connect(cfg.db_path)
    con.execute(
        """
        INSERT INTO plaid_accounts(
          account_id, item_id, institution_name, name, type, subtype,
          current_balance, iso_currency_code, balance_mode, balance_as_of
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "acct-1",
            "item-1",
            "Wells Fargo",
            "Everyday Checking",
            "depository",
            "checking",
            100,
            "USD",
            "cached",
            "2026-05-30T12:00:00+00:00",
        ),
    )
    con.commit()
    con.close()

    status = actions.account_labels_status(cfg)
    assert status["ok"] is True
    assert status["accounts"][0]["account_group"] == "cash"
    assert status["accounts"][0]["owner"] == "self"
    assert status["accounts"][0]["export_enabled"] is True

    saved = actions.save_account_labels(
        cfg,
        {
            "accounts": [
                {
                    "account_id": "acct-1",
                    "export_enabled": False,
                    "label": "Parents checking",
                    "owner": "parents",
                    "account_group": "cash",
                    "include_in_net_worth": True,
                    "parent_draw_source": True,
                }
            ]
        },
    )
    assert saved["ok"] is True
    data = yaml.safe_load((cfg.trackers_dir / "plaid" / "account_labels.yaml").read_text())
    assert data["accounts"]["acct-1"]["owner"] == "parents"
    assert data["accounts"]["acct-1"]["export_enabled"] is False
    assert data["accounts"]["acct-1"]["parent_draw_source"] is True
    assert data["accounts"]["acct-1"]["include_in_net_worth"] is False
    con = connect(cfg.db_path)
    assert con.execute("SELECT export_enabled FROM plaid_account_exports WHERE account_id='acct-1'").fetchone()[0] == 0
    con.close()


def test_account_label_save_derives_finance_flags_from_owner(tmp_root):
    actions = _load_module("actions.py", "plaid_actions_label_flags_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (PLAID_DIR / "schema.sql").read_text())
    con = connect(cfg.db_path)
    con.execute(
        """
        INSERT INTO plaid_accounts(
          account_id, item_id, institution_name, name, type, subtype,
          current_balance, iso_currency_code, balance_mode, balance_as_of
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "acct-1",
            "item-1",
            "Wells Fargo",
            "Everyday Checking",
            "depository",
            "checking",
            100,
            "USD",
            "cached",
            "2026-05-30T12:00:00+00:00",
        ),
    )
    con.commit()
    con.close()

    actions.save_account_labels(
        cfg,
        {
            "accounts": [
                {
                    "account_id": "acct-1",
                    "label": "Self checking",
                    "owner": "self",
                    "account_group": "cash",
                    "include_in_net_worth": False,
                    "parent_draw_source": True,
                }
            ]
        },
    )

    data = yaml.safe_load((cfg.trackers_dir / "plaid" / "account_labels.yaml").read_text())
    assert data["accounts"]["acct-1"]["include_in_net_worth"] is True
    assert data["accounts"]["acct-1"]["parent_draw_source"] is False


def test_load_items_falls_back_to_latest_backup(tmp_root):
    actions = _load_module("actions.py", "plaid_actions_fallback_seed_test")
    ingest = _load_module("ingest.py", "plaid_ingest_fallback_test")
    cfg = Config(root=tmp_root)
    actions._save_item(
        cfg,
        {
            "item_id": "item-1",
            "access_token": "access-development-1",
            "institution_name": "Wells Fargo",
            "created_at": "2026-05-30T12:00:00+00:00",
        },
    )
    (tmp_root / "state" / "plaid" / "items.json").unlink()

    tracker = Tracker(name="plaid", cfg=cfg, manifest=None)
    items = ingest._load_items(tracker)

    assert len(items) == 1
    assert items[0]["access_token"] == "access-development-1"


def test_flatten_transaction_extracts_personal_finance_category():
    ingest = _load_module("ingest.py", "plaid_ingest_flatten_test")
    row = ingest._flatten_transaction(
        {
            "transaction_id": "txn-1",
            "account_id": "acct-1",
            "date": "2026-05-29",
            "name": "SQ COFFEE",
            "merchant_name": "Coffee",
            "amount": 4.5,
            "pending": False,
            "category": ["Food and Drink", "Coffee Shop"],
            "personal_finance_category": {
                "primary": "FOOD_AND_DRINK",
                "detailed": "FOOD_AND_DRINK_COFFEE",
                "confidence_level": "VERY_HIGH",
            },
        },
        {"item_id": "item-1"},
    )
    assert row["transaction_id"] == "txn-1"
    assert row["item_id"] == "item-1"
    assert row["pending"] == 0
    assert row["personal_finance_primary"] == "FOOD_AND_DRINK"
    assert row["personal_finance_detailed"] == "FOOD_AND_DRINK_COFFEE"
    assert json.loads(row["category"]) == ["Food and Drink", "Coffee Shop"]


def test_products_not_supported_is_skippable_investment_error():
    ingest = _load_module("ingest.py", "plaid_ingest_skippable_test")

    assert ingest._is_skippable_investment_error(
        RuntimeError(
            'Plaid /investments/holdings/get error PRODUCTS_NOT_SUPPORTED: '
            'the following products are not supported by this institution: ["investments"]'
        )
    )


def test_sync_no_items_is_noop(tmp_root, monkeypatch):
    ingest = _load_module("ingest.py", "plaid_ingest_noop_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (PLAID_DIR / "schema.sql").read_text())
    monkeypatch.setenv("PLAID_CLIENT_ID", "cid")
    monkeypatch.setenv("PLAID_SECRET", "secret")

    tracker = Tracker(name="plaid", cfg=cfg, manifest=None)
    ingest.sync(tracker)

    con = connect(cfg.db_path)
    assert con.execute("SELECT COUNT(*) FROM plaid_items").fetchone()[0] == 0
    con.close()


def test_finance_model_materializes_labels_cashflow_net_worth_and_parent_draws(tmp_root):
    ingest = _load_module("ingest.py", "plaid_ingest_finance_model_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (PLAID_DIR / "schema.sql").read_text())
    tracker = Tracker(name="plaid", cfg=cfg, manifest=None)

    con = connect(cfg.db_path)
    con.executemany(
        """
        INSERT INTO plaid_accounts(
          account_id, item_id, institution_name, name, type, subtype,
          current_balance, available_balance, iso_currency_code, balance_mode, balance_as_of
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("cash-1", "item-1", "Bank", "Checking", "depository", "checking", 1000, 1000, "USD", "cached", "2026-05-30T12:00:00+00:00"),
            ("card-1", "item-1", "Bank", "Credit Card", "credit", "credit card", 80, 920, "USD", "cached", "2026-05-30T12:00:00+00:00"),
            ("inv-1", "item-1", "Broker", "Brokerage", "investment", "brokerage", 2000, None, "USD", "investments", "2026-05-30T12:00:00+00:00"),
            ("parent-1", "item-1", "Parents Bank", "Parents Checking", "depository", "checking", 500, 500, "USD", "cached", "2026-05-30T12:00:00+00:00"),
        ],
    )
    con.executemany(
        """
        INSERT INTO plaid_transactions(
          transaction_id, item_id, account_id, date, name, merchant_name, amount,
          iso_currency_code, pending, personal_finance_primary, personal_finance_detailed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("txn-spend", "item-1", "card-1", "2026-05-29", "Coffee", "Coffee", 25, "USD", 0, "FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE"),
            ("txn-pay-bank", "item-1", "cash-1", "2026-05-29", "CARD AUTOPAY", None, 80, "USD", 0, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            ("txn-pay-card", "item-1", "card-1", "2026-05-29", "AUTOMATIC PAYMENT", None, -80, "USD", 0, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            ("txn-parent", "item-1", "parent-1", "2026-05-29", "Zelle to Self", "Zelle", 40, "USD", 0, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
        ],
    )
    con.commit()
    con.close()

    label_path = cfg.trackers_dir / "plaid" / "account_labels.yaml"
    label_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.write_text(
        yaml.safe_dump(
            {
                "accounts": {
                    "parent-1": {
                        "owner": "parents",
                        "account_group": "cash",
                        "label": "Parents Checking",
                        "include_in_net_worth": True,
                        "parent_draw_source": True,
                    }
                }
            }
        )
    )

    ingest._materialize_finance_model(tracker, "2026-05-30T12:00:00+00:00")

    con = connect(cfg.db_path)
    assert con.execute("SELECT owner FROM plaid_account_labels WHERE account_id='parent-1'").fetchone()[0] == "parents"
    all_cashflow = con.execute(
        "SELECT income, spending, net, parent_draw, credit_card_payments, internal_transfers "
        "FROM plaid_daily_cashflow WHERE date='2026-05-29' AND owner='all'"
    ).fetchone()
    assert all_cashflow == (0.0, 25.0, -25.0, 40.0, 80.0, 40.0)
    self_worth = con.execute(
        "SELECT cash, investments, credit_card_debt, net_worth "
        "FROM plaid_daily_net_worth WHERE date='2026-05-30' AND owner='self'"
    ).fetchone()
    assert self_worth == (1000.0, 2000.0, 80.0, 2920.0)
    assert con.execute("SELECT amount FROM plaid_parent_draws WHERE transaction_id='txn-parent'").fetchone()[0] == 40.0
    con.close()


def test_plaid_finance_export_views_normalize_contract(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (PLAID_DIR / "schema.sql").read_text())
    con = connect(cfg.db_path)
    con.executemany(
        """
        INSERT INTO plaid_accounts(
          account_id, item_id, institution_name, name, type, subtype,
          current_balance, available_balance, iso_currency_code, balance_mode, balance_as_of
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("cash-1", "item-1", "Bank", "Checking", "depository", "checking", 1000, 900, "USD", "cached", "2026-05-31T00:00:00+00:00"),
            ("parent-1", "item-1", "Bank", "Parent Checking", "depository", "checking", 500, 500, "USD", "cached", "2026-05-31T00:00:00+00:00"),
            ("hidden-1", "item-1", "Bank", "Hidden Checking", "depository", "checking", 50, 50, "USD", "cached", "2026-05-31T00:00:00+00:00"),
        ],
    )
    con.executemany(
        """
        INSERT INTO plaid_account_labels(
          account_id, owner, account_group, label, include_in_net_worth, parent_draw_source, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("cash-1", "self", "cash", "Self Checking", 0, 1, "2026-05-31T00:00:00+00:00"),
            ("parent-1", "parents", "cash", "Parent Checking", 1, 0, "2026-05-31T00:00:00+00:00"),
            ("hidden-1", "self", "cash", "Hidden Checking", 1, 0, "2026-05-31T00:00:00+00:00"),
        ],
    )
    con.execute(
        """
        INSERT INTO plaid_account_exports(account_id, export_enabled, updated_at)
        VALUES (?, ?, ?)
        """,
        ("hidden-1", 0, "2026-05-31T00:00:00+00:00"),
    )
    con.executemany(
        """
        INSERT INTO plaid_transactions(
          transaction_id, item_id, account_id, date, name, merchant_name, amount,
          pending, personal_finance_primary, personal_finance_detailed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("pay", "item-1", "cash-1", "2026-05-30", "Autopay", "Visa", 100, 0, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
            ("transfer", "item-1", "parent-1", "2026-05-30", "Transfer", "Bank", 40, 0, "TRANSFER_OUT", "TRANSFER_OUT_ACCOUNT_TRANSFER"),
        ],
    )
    con.commit()

    parent = con.execute(
        """
        SELECT owner, include_in_net_worth, parent_draw_source
        FROM plaid_finance_accounts_export
        WHERE source_account_id='parent-1'
        """
    ).fetchone()
    assert parent == ("parents", 0, 1)
    self_flags = con.execute(
        """
        SELECT include_in_net_worth, parent_draw_source
        FROM plaid_finance_accounts_export
        WHERE source_account_id='hidden-1'
        """
    ).fetchone()
    assert self_flags is None
    flags = {
        row[0]: (row[1], row[2])
        for row in con.execute(
            """
            SELECT source_transaction_id, is_credit_card_payment, is_internal_transfer
            FROM plaid_finance_transactions_export
            """
        ).fetchall()
    }
    assert flags["pay"] == (1, 0)
    assert con.execute(
        "SELECT is_internal_transfer FROM plaid_finance_transactions_export WHERE source_transaction_id='transfer'"
    ).fetchone()[0] == 1
    con.close()
