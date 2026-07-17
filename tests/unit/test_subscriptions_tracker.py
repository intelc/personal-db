import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, connect, init_db
from personal_db.core.tracker import Tracker
from personal_db.services.daemon.http import build_app

ROOT = Path(__file__).resolve().parents[2]
FINANCE_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "finance"
FINANCE_APP_DIR = ROOT / "src" / "personal_db" / "templates" / "apps" / "finance"
SUBSCRIPTIONS_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "subscriptions"
SCREEN_TIME_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "screen_time"
MOSSPATH_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "mosspath_lite"
CHROME_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "chrome_history"


def _load_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SUBSCRIPTIONS_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_db(cfg: Config) -> None:
    init_db(cfg.db_path)
    for path in (
        FINANCE_DIR / "schema.sql",
        SUBSCRIPTIONS_DIR / "schema.sql",
        SCREEN_TIME_DIR / "schema.sql",
        MOSSPATH_DIR / "schema.sql",
        CHROME_DIR / "schema.sql",
    ):
        apply_tracker_schema(cfg.db_path, path.read_text())

    con = connect(cfg.db_path)
    con.execute(
        """
        INSERT INTO finance_transactions(
          finance_transaction_id, source, source_transaction_id,
          finance_account_id, source_account_id, date, name, merchant_name,
          amount, pending, category, owner, account_group,
          is_credit_card_payment, is_internal_transfer
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "plaid:openai-1",
            "plaid",
            "openai-1",
            "plaid:checking",
            "checking",
            "2026-05-01",
            "OpenAI",
            "OpenAI",
            20.0,
            0,
            "Software",
            "self",
            "cash",
            0,
            0,
        ),
    )
    con.execute(
        """
        INSERT INTO finance_transaction_user_categories(
          finance_transaction_id, user_category, note, updated_at
        ) VALUES (?, ?, ?, ?)
        """,
        ("plaid:openai-1", "Subscriptions", "manual", "2026-05-02T00:00:00+00:00"),
    )
    con.execute(
        """
        INSERT INTO screen_time_app_usage(bundle_id, start_at, end_at, seconds)
        VALUES (?, ?, ?, ?)
        """,
        (
            "com.openai.chat",
            "2026-05-03T10:00:00+00:00",
            "2026-05-03T11:00:00+00:00",
            3600,
        ),
    )
    con.execute(
        """
        INSERT INTO screen_time_app_names(bundle_id, app_name, resolved_at)
        VALUES (?, ?, ?)
        """,
        ("com.openai.chat", "ChatGPT", "2026-05-03T11:00:00+00:00"),
    )
    con.execute(
        """
        INSERT INTO chrome_visits(
          visit_id, profile, url, title, domain, visited_at, duration_seconds, transition
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Default",
            "https://chatgpt.com/",
            "ChatGPT",
            "chatgpt.com",
            "2026-05-04T10:00:00+00:00",
            1800,
            0,
        ),
    )
    con.execute(
        """
        INSERT INTO mosspath_lite_events(
          id, timestamp, action_type, app_name, bundle_id, browser_domain, browser_title
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "mp-1",
            "2026-05-04T10:05:00+00:00",
            "app_visit",
            "Google Chrome",
            "com.google.Chrome",
            "chatgpt.com",
            "ChatGPT",
        ),
    )
    con.commit()
    con.close()


def test_subscriptions_sync_materializes_charges_usage_and_periods(tmp_root, frozen_datetime):
    # _status()/_period_rows() compute subscription age and period coverage
    # off datetime.now(); the fixture charge below is dated 2026-05-01, so
    # freeze "now" nearby rather than let real time drift the subscription
    # into "stale". Must freeze before _load_module() executes the module's
    # `from datetime import ... datetime`.
    frozen_datetime(2026, 6, 15)
    ingest = _load_module("ingest.py", "subscriptions_ingest_test")
    cfg = Config(root=tmp_root)
    _seed_db(cfg)

    ingest.sync(Tracker("subscriptions", cfg, manifest=None))

    con = connect(cfg.db_path)
    entity = con.execute(
        """
        SELECT label, charge_count, latest_amount, cadence, status
        FROM subscription_entities
        """
    ).fetchone()
    assert entity == ("OpenAI / ChatGPT", 1, 20.0, "single", "active")
    assert con.execute("SELECT COUNT(*) FROM subscription_charges").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM subscription_usage_evidence").fetchone()[0] == 3
    period = con.execute(
        """
        SELECT cost, usage_minutes, active_days, event_count, utilization_label
        FROM subscription_utilization_periods
        """
    ).fetchone()
    assert period[0] == 20.0
    assert period[1] == 90.0
    assert period[2] == 2
    assert period[3] == 3
    assert period[4] in {"medium", "unknown"}
    con.close()


def test_subscriptions_app_renders(tmp_root):
    ingest = _load_module("ingest.py", "subscriptions_ingest_app_test")
    cfg = Config(root=tmp_root)
    _seed_db(cfg)
    ingest.sync(Tracker("subscriptions", cfg, manifest=None))

    client = TestClient(build_app(cfg))
    r = client.get("/a/subscriptions")
    assert r.status_code == 200
    assert "Subscriptions Overview" in r.text
    assert "OpenAI / ChatGPT" in r.text


def test_subscriptions_app_can_mark_false_positive_in_finance_state(tmp_root):
    ingest = _load_module("ingest.py", "subscriptions_ingest_action_test")
    cfg = Config(root=tmp_root)
    _seed_db(cfg)
    apply_tracker_schema(cfg.db_path, (FINANCE_APP_DIR / "schema.sql").read_text())
    ingest.sync(Tracker("subscriptions", cfg, manifest=None))

    con = connect(cfg.db_path)
    subscription_id = con.execute("SELECT subscription_id FROM subscription_entities").fetchone()[0]
    con.close()

    client = TestClient(build_app(cfg))
    r = client.post(
        "/api/apps/subscriptions/actions/mark_not_subscription",
        data={
            "subscription_id": subscription_id,
            "merchant": "OpenAI",
            "label": "OpenAI / ChatGPT",
        },
        headers={"referer": "/a/subscriptions/subscriptions"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    con = connect(cfg.db_path)
    assert (
        con.execute(
            "SELECT COUNT(*) FROM subscription_entities WHERE subscription_id=?",
            (subscription_id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        con.execute(
            """
            SELECT user_category
            FROM finance_transaction_user_categories
            WHERE finance_transaction_id='plaid:openai-1'
            """
        ).fetchone()[0]
        == "Entertainment"
    )
    assert (
        con.execute(
            """
            SELECT bucket
            FROM app_finance_burn_rules
            WHERE rule_key='user:not_subscription:merchant:openai'
            """
        ).fetchone()[0]
        == "entertainment"
    )
    con.close()


def test_subscriptions_sync_reads_legacy_finance_subscription_bucket(tmp_root):
    ingest = _load_module("ingest.py", "subscriptions_ingest_burn_bridge_test")
    cfg = Config(root=tmp_root)
    _seed_db(cfg)
    apply_tracker_schema(cfg.db_path, (FINANCE_APP_DIR / "schema.sql").read_text())

    con = connect(cfg.db_path)
    con.execute(
        """
        INSERT INTO finance_transactions(
          finance_transaction_id, source, source_transaction_id,
          finance_account_id, source_account_id, date, name, merchant_name,
          amount, pending, category, owner, account_group,
          is_credit_card_payment, is_internal_transfer
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "plaid:netflix-1",
            "plaid",
            "netflix-1",
            "plaid:checking",
            "checking",
            "2026-05-02",
            "Netflix",
            "Netflix",
            15.49,
            0,
            "ENTERTAINMENT_TV_AND_MOVIES",
            "self",
            "cash",
            0,
            0,
        ),
    )
    con.commit()
    con.close()

    ingest.sync(Tracker("subscriptions", cfg, manifest=None))

    con = connect(cfg.db_path)
    row = con.execute(
        """
        SELECT e.label, c.category_source, c.match_reason
        FROM subscription_charges c
        JOIN subscription_entities e ON e.subscription_id = c.subscription_id
        WHERE c.finance_transaction_id='plaid:netflix-1'
        """
    ).fetchone()
    con.close()
    assert row == ("Netflix", "finance_burn_rule", "subscription pattern")


def test_subscriptions_sync_respects_finance_not_subscription_rules(tmp_root):
    ingest = _load_module("ingest.py", "subscriptions_ingest_not_subscription_test")
    cfg = Config(root=tmp_root)
    _seed_db(cfg)
    apply_tracker_schema(cfg.db_path, (FINANCE_APP_DIR / "schema.sql").read_text())

    con = connect(cfg.db_path)
    rows = [
        ("plaid:amc-1", "AMC Theatres", "AMC Theatres", 50.36, "ENTERTAINMENT_TV_AND_MOVIES"),
        ("plaid:cinemark-1", "Cinemark Theatres", "Cinemark Theatres", 32.0, "ENTERTAINMENT_TV_AND_MOVIES"),
        (
            "plaid:prime-video-1",
            "Amazon Prime Video",
            "Amazon Prime Video",
            14.83,
            "ENTERTAINMENT_TV_AND_MOVIES",
        ),
        ("plaid:apple-1", "Apple", "Apple", 9.99, "GENERAL_MERCHANDISE_ELECTRONICS"),
        ("plaid:apple-2", "Apple", "Apple", 29.99, "GENERAL_MERCHANDISE_ELECTRONICS"),
    ]
    con.executemany(
        """
        INSERT INTO finance_transactions(
          finance_transaction_id, source, source_transaction_id,
          finance_account_id, source_account_id, date, name, merchant_name,
          amount, pending, category, owner, account_group,
          is_credit_card_payment, is_internal_transfer
        ) VALUES (?, 'plaid', ?, 'plaid:checking', 'checking', '2026-05-03', ?, ?, ?, 0, ?, 'self', 'cash', 0, 0)
        """,
        [(txn_id, txn_id, name, merchant, amount, category) for txn_id, name, merchant, amount, category in rows],
    )
    con.commit()
    con.close()

    ingest.sync(Tracker("subscriptions", cfg, manifest=None))

    con = connect(cfg.db_path)
    labels = {
        str(row[0])
        for row in con.execute("SELECT label FROM subscription_entities").fetchall()
    }
    apple = con.execute(
        """
        SELECT monthly_avg_amount
        FROM subscription_entities
        WHERE label='Apple'
        """
    ).fetchone()
    con.close()
    assert "AMC Theatres" not in labels
    assert "Cinemark Theatres" not in labels
    assert "Amazon Prime Video" not in labels
    assert apple == (39.98,)


def test_subscriptions_sync_splits_same_merchant_recurring_series_by_amount(tmp_root):
    ingest = _load_module("ingest.py", "subscriptions_ingest_series_split_test")
    cfg = Config(root=tmp_root)
    _seed_db(cfg)
    apply_tracker_schema(cfg.db_path, (FINANCE_APP_DIR / "schema.sql").read_text())

    con = connect(cfg.db_path)
    apple_rows = [
        ("apple-icloud-jan", "2026-01-05", 9.99),
        ("apple-icloud-feb", "2026-02-05", 9.99),
        ("apple-icloud-mar", "2026-03-05", 9.99),
        ("apple-storage-jan", "2026-01-12", 2.99),
        ("apple-storage-feb", "2026-02-12", 2.99),
        ("apple-storage-mar", "2026-03-12", 2.99),
        ("apple-onetime", "2026-03-20", 49.99),
    ]
    con.executemany(
        """
        INSERT INTO finance_transactions(
          finance_transaction_id, source, source_transaction_id,
          finance_account_id, source_account_id, date, name, merchant_name,
          amount, pending, category, owner, account_group,
          is_credit_card_payment, is_internal_transfer
        ) VALUES (?, 'plaid', ?, 'plaid:checking', 'checking', ?, 'Apple', 'Apple', ?, 0,
                  'GENERAL_MERCHANDISE_ELECTRONICS', 'self', 'cash', 0, 0)
        """,
        [(f"plaid:{source_id}", source_id, day, amount) for source_id, day, amount in apple_rows],
    )
    con.commit()
    con.close()

    ingest.sync(Tracker("subscriptions", cfg, manifest=None))

    con = connect(cfg.db_path)
    rows = con.execute(
        """
        SELECT label, charge_count, typical_amount, monthly_avg_amount, expected_day
        FROM subscription_entities
        WHERE label LIKE 'Apple%'
        ORDER BY typical_amount
        """
    ).fetchall()
    linked_charges = con.execute(
        """
        SELECT COUNT(*)
        FROM subscription_charges c
        JOIN subscription_entities e ON e.subscription_id = c.subscription_id
        WHERE e.label LIKE 'Apple%'
        """
    ).fetchone()[0]
    con.close()
    assert rows == [
        ("Apple $2.99", 3, 2.99, 2.99, 12),
        ("Apple $9.99", 3, 9.99, 9.99, 5),
    ]
    assert linked_charges == 6
