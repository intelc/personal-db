import importlib.util
from pathlib import Path

from personal_db.config import Config
from personal_db.db import apply_tracker_schema, connect, init_db
from personal_db.tracker import Tracker

ROOT = Path(__file__).resolve().parents[2]
FINANCE_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "finance"
PLAID_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "plaid"
MONARCH_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "monarch"


def _load_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, FINANCE_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_finance_manifest_loads():
    from personal_db.manifest import load_manifest

    manifest = load_manifest(FINANCE_DIR / "manifest.yaml")
    assert manifest.name == "finance"
    assert manifest.permission_type == "none"
    assert "finance_accounts" in manifest.schema.tables
    assert "finance_daily_cashflow" in manifest.schema.tables


def test_finance_sync_combines_sources_and_normalizes_cashflow(tmp_root):
    ingest = _load_module("ingest.py", "finance_ingest_sync_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (PLAID_DIR / "schema.sql").read_text())
    apply_tracker_schema(cfg.db_path, (MONARCH_DIR / "schema.sql").read_text())
    apply_tracker_schema(cfg.db_path, (FINANCE_DIR / "schema.sql").read_text())

    con = connect(cfg.db_path)
    con.executemany(
        """
        INSERT INTO plaid_accounts(
          account_id, item_id, institution_name, name, type, subtype,
          current_balance, available_balance, iso_currency_code, balance_mode, balance_as_of
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("plaid-cash", "item-1", "Bank", "Checking", "depository", "checking", 1000, 900, "USD", "cached", "2026-05-31T00:00:00+00:00"),
            ("plaid-card", "item-1", "Cards", "Visa", "credit", "credit card", 200, None, "USD", "cached", "2026-05-31T00:00:00+00:00"),
            ("plaid-invest", "item-1", "Broker", "Brokerage", "investment", "brokerage", 1200, None, "USD", "investments", "2026-05-31T00:00:00+00:00"),
        ],
    )
    con.executemany(
        """
        INSERT INTO plaid_account_labels(
          account_id, owner, account_group, label, include_in_net_worth, parent_draw_source, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("plaid-cash", "self", "cash", "Bank Checking", 1, 0, "2026-05-31T00:00:00+00:00"),
            ("plaid-card", "self", "credit_card", "Cards Visa", 1, 0, "2026-05-31T00:00:00+00:00"),
            ("plaid-invest", "self", "investments", "Brokerage", 1, 0, "2026-05-31T00:00:00+00:00"),
        ],
    )
    con.execute(
        """
        INSERT INTO plaid_investment_securities(
          security_id, name, ticker_symbol, type
        ) VALUES ('sec-vti', 'Total Market ETF', 'VTI', 'etf')
        """
    )
    con.executemany(
        """
        INSERT INTO plaid_investment_holdings(
          snapshot_id, item_id, account_id, security_id, as_of, quantity,
          cost_basis, institution_price, institution_value
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("snap-old", "item-1", "plaid-invest", "sec-vti", "2026-05-30T00:00:00+00:00", 10, 900, 100, 1000),
            ("snap-new", "item-1", "plaid-invest", "sec-vti", "2026-05-31T00:00:00+00:00", 10, 900, 120, 1200),
        ],
    )
    con.executemany(
        """
        INSERT INTO plaid_transactions(
          transaction_id, item_id, account_id, date, name, merchant_name, amount,
          pending, personal_finance_primary, personal_finance_detailed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("plaid-income", "item-1", "plaid-cash", "2026-05-30", "Payroll", "Employer", -100, 0, "INCOME", "INCOME_WAGES"),
            ("plaid-card-pay", "item-1", "plaid-cash", "2026-05-30", "Autopay", "Visa", 50, 0, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"),
        ],
    )
    con.executemany(
        """
        INSERT INTO monarch_accounts(
          account_id, display_name, type_name, subtype_name, institution_name,
          current_balance, display_balance, display_last_updated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("mon-self", "HSBC Checking", "bank", "checking", "HSBC", 300, 300, "2026-05-31T00:00:00+00:00", "2026-05-31T00:00:00+00:00"),
            ("mon-parent", "Parent Checking", "bank", "checking", "HSBC", 5000, 5000, "2026-05-31T00:00:00+00:00", "2026-05-31T00:00:00+00:00"),
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
            ("mon-self", "HSBC Checking", "self", "cash", 1, 0, "2026-05-31T00:00:00+00:00"),
            ("mon-parent", "Parent Checking", "parents", "cash", 0, 1, "2026-05-31T00:00:00+00:00"),
        ],
    )
    con.executemany(
        """
        INSERT INTO monarch_account_exports(account_id, export_enabled, updated_at)
        VALUES (?, ?, ?)
        """,
        [
            ("mon-self", 1, "2026-05-31T00:00:00+00:00"),
            ("mon-parent", 1, "2026-05-31T00:00:00+00:00"),
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
            ("mon-food", "mon-self", "HSBC Checking", "2026-05-30", -10, 0, "Cafe", "Restaurants", 0),
            ("mon-parent-spend", "mon-parent", "Parent Checking", "2026-05-30", -40, 0, "Pharmacy", "Health", 0),
            ("mon-parent-transfer", "mon-parent", "Parent Checking", "2026-05-30", -25, 0, "Self", "Transfer", 0),
        ],
    )
    con.execute(
        """
        INSERT INTO finance_account_snapshots(
          snapshot_id, date, finance_account_id, source, source_account_id, owner,
          account_group, institution_name, account_name, balance, net_worth_value,
          debt_value, iso_currency_code, include_in_net_worth, as_of
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-05-30:stale-parent-investment",
            "2026-05-30",
            "stale-parent-investment",
            "plaid",
            "old-parent",
            "self",
            "investments",
            "Old Bank",
            "Disabled Parent Brokerage",
            525000,
            525000,
            0,
            "USD",
            1,
            "2026-05-30T00:00:00+00:00",
        ),
    )
    con.commit()
    con.close()

    ingest.sync(Tracker("finance", cfg, manifest=None))

    con = connect(cfg.db_path)
    assert con.execute("SELECT COUNT(*) FROM finance_accounts").fetchone()[0] == 5
    assert con.execute("SELECT COUNT(*) FROM finance_transactions").fetchone()[0] == 5
    holding = con.execute(
        "SELECT ticker, value FROM finance_holdings WHERE source='plaid'"
    ).fetchone()
    assert holding == ("VTI", 1200.0)
    assert con.execute("SELECT COUNT(*) FROM finance_holding_snapshots WHERE source='plaid'").fetchone()[0] == 2
    row = con.execute(
        """
        SELECT income, spending, net, parent_draw, credit_card_payments, internal_transfers
        FROM finance_daily_cashflow
        WHERE date='2026-05-30' AND owner='all'
        """
    ).fetchone()
    assert row == (100.0, 50.0, 50.0, 65.0, 50.0, 25.0)
    net = con.execute(
        "SELECT cash, investments, credit_card_debt, net_worth FROM finance_daily_net_worth WHERE owner='all' AND date='2026-05-31'"
    ).fetchone()
    assert net == (1300.0, 1200.0, 200.0, 2300.0)
    assert con.execute(
        "SELECT COUNT(*) FROM finance_account_snapshots WHERE finance_account_id='stale-parent-investment'"
    ).fetchone()[0] == 1
    parent = con.execute(
        "SELECT include_in_net_worth, parent_draw_source FROM finance_accounts WHERE source_account_id='mon-parent'"
    ).fetchone()
    assert parent == (0, 1)
    con.close()

    viz = _load_module("visualizations.py", "finance_viz_dashboard_test")
    entries = viz.list_visualizations()
    assert entries[0]["name"] == "Finance app"
    assert "/a/finance" in entries[0]["render"](cfg)


def test_finance_sync_discovers_export_views_without_source_names(tmp_root):
    ingest = _load_module("ingest.py", "finance_ingest_discovery_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (FINANCE_DIR / "schema.sql").read_text())
    con = connect(cfg.db_path)
    con.executescript(
        """
        CREATE TABLE demo_accounts_source(
          source TEXT, source_account_id TEXT, finance_account_id TEXT,
          owner TEXT, account_group TEXT, institution_name TEXT, account_name TEXT,
          current_balance REAL
        );
        CREATE TABLE demo_transactions_source(
          source TEXT, source_transaction_id TEXT, finance_transaction_id TEXT,
          source_account_id TEXT, finance_account_id TEXT, date TEXT,
          name TEXT, amount REAL
        );
        CREATE VIEW demo_finance_accounts_export AS
        SELECT source, source_account_id, finance_account_id, owner, account_group,
               institution_name, account_name, NULL AS mask, NULL AS type, NULL AS subtype,
               current_balance, NULL AS available_balance, 'USD' AS iso_currency_code,
               1 AS include_in_net_worth, 0 AS parent_draw_source,
               '2026-05-31T00:00:00+00:00' AS as_of, NULL AS raw_json
        FROM demo_accounts_source;
        CREATE VIEW demo_finance_transactions_export AS
        SELECT source, source_transaction_id, finance_transaction_id, source_account_id,
               finance_account_id, date, name, NULL AS merchant_name, amount,
               amount AS source_amount, 0 AS pending, NULL AS category,
               0 AS is_credit_card_payment, 0 AS is_internal_transfer, NULL AS raw_json
        FROM demo_transactions_source;
        CREATE VIEW unrelated_finance_accounts_export_backup AS SELECT 1 AS nope;
        """
    )
    con.execute(
        "INSERT INTO demo_accounts_source VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("demo", "acct-1", "demo:acct-1", "self", "cash", "Demo Bank", "Checking", 500),
    )
    con.execute(
        "INSERT INTO demo_transactions_source VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("demo", "txn-1", "demo:txn-1", "acct-1", "demo:acct-1", "2026-05-30", "Coffee", 7),
    )
    con.commit()
    con.close()

    ingest.sync(Tracker("finance", cfg, manifest=None))

    con = connect(cfg.db_path)
    assert con.execute("SELECT source, account_name FROM finance_accounts").fetchone() == (
        "demo",
        "Checking",
    )
    assert con.execute("SELECT source, amount FROM finance_transactions").fetchone() == (
        "demo",
        7.0,
    )
    assert con.execute("SELECT net_worth FROM finance_daily_net_worth WHERE owner='all'").fetchone()[0] == 500.0
    con.close()


def test_finance_sync_clears_materialized_tables_when_no_export_views(tmp_root):
    ingest = _load_module("ingest.py", "finance_ingest_no_exports_test")
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (FINANCE_DIR / "schema.sql").read_text())
    con = connect(cfg.db_path)
    con.execute(
        """
        INSERT INTO finance_accounts(
          finance_account_id, source, source_account_id, owner, account_group,
          current_balance, include_in_net_worth, parent_draw_source, as_of
        ) VALUES ('old:acct', 'old', 'acct', 'self', 'cash', 1, 1, 0, '2026-05-31')
        """
    )
    con.commit()
    con.close()

    ingest.sync(Tracker("finance", cfg, manifest=None))

    con = connect(cfg.db_path)
    assert con.execute("SELECT COUNT(*) FROM finance_accounts").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM finance_daily_net_worth").fetchone()[0] == 0
    con.close()
