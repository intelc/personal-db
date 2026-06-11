import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from personal_db.config import Config
from personal_db.db import apply_tracker_schema, connect, init_db
from personal_db.tracker import Tracker

MONARCH_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "personal_db"
    / "templates"
    / "trackers"
    / "monarch"
)


def _load_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, MONARCH_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_monarch_manifest_loads():
    from personal_db.manifest import load_manifest

    manifest = load_manifest(MONARCH_DIR / "manifest.yaml")
    assert manifest.name == "monarch"
    assert "monarch_accounts" in manifest.schema.tables
    assert "monarch_account_labels" in manifest.schema.tables
    assert "monarch_account_exports" in manifest.schema.tables


def test_monarch_library_imports_and_client_constructs(tmp_root):
    actions = _load_module("actions.py", "monarch_actions_library_test")
    cfg = Config(root=tmp_root)

    result = actions.debug_library(cfg)

    assert result["ok"] is True
    assert "MonarchClient" in result["client"]


def test_totp_generation_uses_rfc_6238_vector():
    parsers = _load_module("parsers.py", "monarch_parsers_totp_test")

    assert parsers.generate_totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", for_time=59, digits=8) == "94287082"


def test_monarch_api_base_uses_current_non_redirecting_host():
    parsers = _load_module("parsers.py", "monarch_parsers_base_url_test")

    assert parsers.BASE_URL == "https://api.monarch.com"


def test_monarch_client_refreshes_expired_token_and_retries(tmp_root, monkeypatch):
    parsers = _load_module("parsers.py", "monarch_parsers_refresh_test")
    session_file = tmp_root / "state" / "monarch" / "session.json"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(json.dumps({"token": "expired-token"}))
    calls = []

    class Response:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body
            self.reason = "reason"
            self.text = json.dumps(body)

        def json(self):
            return self._body

    def fake_post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        if url.endswith("/graphql") and headers.get("Authorization") == "Token expired-token":
            return Response(401, {"detail": "Invalid token."})
        if url.endswith("/auth/login/"):
            assert json["username"] == "me@example.com"
            assert json["password"] == "secret"
            assert "Authorization" not in headers
            return Response(200, {"token": "fresh-token"})
        if url.endswith("/graphql") and headers.get("Authorization") == "Token fresh-token":
            return Response(200, {"data": {"ok": True}})
        raise AssertionError(f"unexpected request {url} {headers}")

    monkeypatch.setattr(parsers, "requests", SimpleNamespace(post=fake_post))
    mm = parsers.MonarchClient(
        session_file=session_file,
        email="me@example.com",
        password="secret",
    )

    assert mm.gql("TestOperation", "query TestOperation { ok }") == {"ok": True}
    assert json.loads(session_file.read_text())["token"] == "fresh-token"
    assert [call[0] for call in calls] == [
        f"{parsers.BASE_URL}/graphql",
        f"{parsers.BASE_URL}/auth/login/",
        f"{parsers.BASE_URL}/graphql",
    ]


def test_monarch_client_reports_missing_credentials_for_expired_token(tmp_root, monkeypatch):
    parsers = _load_module("parsers.py", "monarch_parsers_refresh_missing_creds_test")
    session_file = tmp_root / "state" / "monarch" / "session.json"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(json.dumps({"token": "expired-token"}))

    class Response:
        status_code = 401
        reason = "unauthorized"
        text = '{"detail":"Invalid token."}'

        def json(self):
            return {"detail": "Invalid token."}

    monkeypatch.setattr(
        parsers,
        "requests",
        SimpleNamespace(post=lambda *args, **kwargs: Response()),
    )
    mm = parsers.MonarchClient(session_file=session_file)

    try:
        mm.gql("TestOperation", "query TestOperation { ok }")
    except parsers.MonarchLoginError as exc:
        assert "MONARCH_EMAIL/MONARCH_PASSWORD" in str(exc)
    else:
        raise AssertionError("expected MonarchLoginError")


def test_flatten_account_and_transaction():
    ingest = _load_module("ingest.py", "monarch_ingest_flatten_test")

    account = ingest._flatten_account(
        {
            "id": "acct-1",
            "displayName": "HSBC Checking",
            "mask": "1234",
            "type": {"name": "bank", "display": "Bank"},
            "subtype": {"name": "checking", "display": "Checking"},
            "institution": {"id": "inst-1", "name": "HSBC"},
            "currentBalance": 123.45,
            "includeInNetWorth": True,
            "isAsset": True,
        },
        "2026-05-31T00:00:00+00:00",
    )
    assert account["account_id"] == "acct-1"
    assert account["institution_name"] == "HSBC"
    assert account["type_name"] == "bank"
    assert account["include_in_net_worth"] == 1
    assert json.loads(account["raw_json"])["displayName"] == "HSBC Checking"

    txn = ingest._flatten_transaction(
        {
            "id": "txn-1",
            "date": "2026-05-30",
            "amount": 12.34,
            "pending": False,
            "merchant": {"id": "m-1", "name": "Coffee"},
            "category": {"id": "c-1", "name": "Restaurants"},
            "account": {"id": "acct-1", "displayName": "HSBC Checking"},
        }
    )
    assert txn["transaction_id"] == "txn-1"
    assert txn["account_id"] == "acct-1"
    assert txn["merchant_name"] == "Coffee"
    assert txn["category_name"] == "Restaurants"


def test_flatten_balances_accepts_monarch_numeric_series():
    ingest = _load_module("ingest.py", "monarch_ingest_balance_series_test")

    rows = ingest._flatten_balances(
        {"id": "acct-1", "recentBalances": [100.0, 101.5, None, 99]},
        "2026-05-31T00:00:00+00:00",
        start_date="2026-05-29",
        end_date="2026-06-01",
    )

    assert rows == [
        {
            "balance_id": "acct-1:2026-05-29",
            "account_id": "acct-1",
            "date": "2026-05-29",
            "balance": 100.0,
            "updated_at": "2026-05-31T00:00:00+00:00",
        },
        {
            "balance_id": "acct-1:2026-05-30",
            "account_id": "acct-1",
            "date": "2026-05-30",
            "balance": 101.5,
            "updated_at": "2026-05-31T00:00:00+00:00",
        },
        {
            "balance_id": "acct-1:2026-06-01",
            "account_id": "acct-1",
            "date": "2026-06-01",
            "balance": 99,
            "updated_at": "2026-05-31T00:00:00+00:00",
        },
    ]


def test_flatten_balances_skips_downsampled_undated_series():
    ingest = _load_module("ingest.py", "monarch_ingest_balance_downsample_test")

    assert (
        ingest._flatten_balances(
            {"id": "acct-1", "recentBalances": [100.0, 101.5]},
            "2026-05-31T00:00:00+00:00",
            start_date="2026-05-01",
            end_date="2026-05-31",
        )
        == []
    )


def test_account_export_actions_round_trip(tmp_root):
    actions = _load_module("actions.py", "monarch_actions_exports_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (MONARCH_DIR / "schema.sql").read_text())
    con = connect(cfg.db_path)
    con.execute(
        """
        INSERT INTO monarch_accounts(
          account_id, display_name, mask, type_name, type_display, subtype_name,
          subtype_display, institution_name, current_balance, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "acct-1",
            "HSBC Checking",
            "1234",
            "bank",
            "Bank",
            "checking",
            "Checking",
            "HSBC",
            123.45,
            "2026-05-31T00:00:00+00:00",
        ),
    )
    con.commit()
    con.close()

    status = actions.accounts_status(cfg)
    assert status["ok"] is True
    assert status["accounts"][0]["account_group"] == "cash"
    assert status["accounts"][0]["export_enabled"] is False

    result = actions.save_account_exports(
        cfg,
        {
            "accounts": [
                {
                    "account_id": "acct-1",
                    "export_enabled": True,
                    "owner": "parents",
                    "account_group": "cash",
                    "include_in_net_worth": True,
                    "parent_draw_source": False,
                }
            ]
        },
    )
    assert result["ok"] is True
    data = yaml.safe_load((cfg.trackers_dir / "monarch" / "account_exports.yaml").read_text())
    assert data["accounts"]["acct-1"]["export_enabled"] is True
    assert data["accounts"]["acct-1"]["owner"] == "parents"
    assert data["accounts"]["acct-1"]["include_in_net_worth"] is False
    assert data["accounts"]["acct-1"]["parent_draw_source"] is True
    con = connect(cfg.db_path, read_only=True)
    label_row = con.execute(
        "SELECT owner, account_group, include_in_net_worth, parent_draw_source FROM monarch_account_labels WHERE account_id = ?",
        ("acct-1",),
    ).fetchone()
    export_row = con.execute(
        "SELECT export_enabled FROM monarch_account_exports WHERE account_id = ?",
        ("acct-1",),
    ).fetchone()
    con.close()
    assert label_row == ("parents", "cash", 0, 1)
    assert export_row == (1,)


def test_monarch_finance_export_views_normalize_contract(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (MONARCH_DIR / "schema.sql").read_text())
    con = connect(cfg.db_path)
    con.executemany(
        """
        INSERT INTO monarch_accounts(
          account_id, display_name, type_name, subtype_name, institution_name,
          current_balance, display_balance, display_last_updated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("enabled-parent", "Parent Checking", "bank", "checking", "HSBC", 1000, 1000, "2026-05-31T00:00:00+00:00", "2026-05-31T00:00:00+00:00"),
            ("disabled-self", "Self Checking", "bank", "checking", "HSBC", 200, 200, "2026-05-31T00:00:00+00:00", "2026-05-31T00:00:00+00:00"),
        ],
    )
    con.executemany(
        """
        INSERT INTO monarch_account_labels(
          account_id, label, owner, account_group, include_in_net_worth,
          parent_draw_source, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("enabled-parent", "Parent Checking", "parents", "cash", 1, 0, "2026-05-31T00:00:00+00:00"),
            ("disabled-self", "Self Checking", "self", "cash", 1, 0, "2026-05-31T00:00:00+00:00"),
        ],
    )
    con.executemany(
        """
        INSERT INTO monarch_account_exports(account_id, export_enabled, updated_at)
        VALUES (?, ?, ?)
        """,
        [
            ("enabled-parent", 1, "2026-05-31T00:00:00+00:00"),
            ("disabled-self", 0, "2026-05-31T00:00:00+00:00"),
        ],
    )
    con.executemany(
        """
        INSERT INTO monarch_transactions(
          transaction_id, account_id, account_name, date, amount, pending,
          merchant_name, category_name, hide_from_reports
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("spend", "enabled-parent", "Parent Checking", "2026-05-30", -25, 0, "Cafe", "Restaurants", 0),
            ("card-pay", "enabled-parent", "Parent Checking", "2026-05-30", -100, 0, "Visa", "Credit Card Payment", 0),
            ("disabled", "disabled-self", "Self Checking", "2026-05-30", -10, 0, "Cafe", "Restaurants", 0),
        ],
    )
    con.commit()

    accounts = con.execute(
        "SELECT source_account_id, owner, include_in_net_worth, parent_draw_source FROM monarch_finance_accounts_export"
    ).fetchall()
    assert accounts == [("enabled-parent", "parents", 0, 1)]
    txns = {
        row[0]: (row[1], row[2])
        for row in con.execute(
            "SELECT source_transaction_id, amount, is_credit_card_payment FROM monarch_finance_transactions_export"
        ).fetchall()
    }
    assert txns["spend"] == (25.0, 0)
    assert txns["card-pay"] == (100.0, 1)
    assert "disabled" not in txns
    con.close()


def test_monarch_prunes_stale_holdings_for_refreshed_accounts(tmp_root):
    ingest = _load_module("ingest.py", "monarch_ingest_prune_holdings_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (MONARCH_DIR / "schema.sql").read_text())
    con = connect(cfg.db_path)
    con.executemany(
        """
        INSERT INTO monarch_holdings(
          holding_id, account_id, security_name, ticker, total_value, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            ("acct-1:current", "acct-1", "Current Fund", "CURR", 100, "2026-05-31T00:00:00+00:00"),
            ("acct-1:stale", "acct-1", "Sold Fund", "SOLD", 50, "2026-01-15T00:00:00+00:00"),
            ("acct-2:untouched", "acct-2", "Other Account", "KEEP", 75, "2026-01-15T00:00:00+00:00"),
        ],
    )
    con.commit()
    con.close()

    tracker = Tracker("monarch", cfg, manifest=None)
    pruned = ingest._prune_holdings_for_accounts(tracker, ["acct-1"], {"acct-1:current"})

    con = connect(cfg.db_path, read_only=True)
    holdings = con.execute("SELECT holding_id FROM monarch_holdings ORDER BY holding_id").fetchall()
    con.close()
    assert pruned == 1
    assert holdings == [("acct-1:current",), ("acct-2:untouched",)]
