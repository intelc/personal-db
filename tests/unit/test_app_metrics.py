"""Unit tests for the `metrics(cfg) -> list[dict]` dashboard-tile contract
implemented by a subset of bundled *app* templates (attention, places,
subscriptions, calendar_reality, finance -- see services/ui/tiles.py's
`build_app_tiles`).

Mirrors test_tracker_metrics.py's fixture pattern: each test hand-creates
the app's mart table(s) with the real production schema (a minimal subset of
columns -- just what the metrics query touches), seeds rows relative to
"now" so the tests stay valid regardless of when they run, loads `metrics`
straight off the bundled app's `views.py` (via `discover_apps`, the same
lookup `build_app_tiles` uses -- so this exercises the exact file that ships,
not a copy), and asserts on the returned metrics.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from personal_db.core.apps import discover_apps
from personal_db.core.config import Config
from personal_db.core.db import init_db


def _setup(tmp_path: Path) -> Config:
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    init_db(cfg.db_path)
    return cfg


def _load_app_metrics_fn(cfg: Config, app_name: str):
    # These tests exercise the packaged template directly; production app
    # discovery intentionally only includes user-installed apps.
    apps = discover_apps(cfg, include_bundled=True)
    definition = apps[app_name]
    path = definition.root / "views.py"
    modname = f"_test_app_metrics_{app_name}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod.metrics


def _by_label(rows: list[dict]) -> dict[str, dict]:
    return {r["label"]: r for r in rows}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# --- attention -------------------------------------------------------------


def _create_notification_impacts(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE notification_impacts ("
        "notification_id TEXT PRIMARY KEY, delivered_at TEXT NOT NULL, "
        "impact TEXT NOT NULL)"
    )


def test_attention_metrics(tmp_path):
    cfg = _setup(tmp_path)
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    _create_notification_impacts(con)
    # This week (7d): 2 acted_on, 1 derailed, 1 ignored, 1 glanced -> 5 total.
    this_week = [
        ("n1", "acted_on"), ("n2", "acted_on"), ("n3", "derailed"),
        ("n4", "ignored"), ("n5", "glanced"),
    ]
    for i, (nid, impact) in enumerate(this_week):
        con.execute(
            "INSERT INTO notification_impacts(notification_id, delivered_at, impact) "
            "VALUES (?, ?, ?)",
            (nid, _iso(now - timedelta(days=1, hours=i)), impact),
        )
    # Prior week (7-14d ago): 3 derailed -> derailment count should drop.
    for i in range(3):
        con.execute(
            "INSERT INTO notification_impacts(notification_id, delivered_at, impact) "
            "VALUES (?, ?, 'derailed')",
            (f"p{i}", _iso(now - timedelta(days=10, hours=i))),
        )
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "attention")
    rows = _by_label(metrics(cfg))
    assert rows["Notifications (7d)"]["value"] == "5"
    assert rows["Acted on"]["value"] == "60%"
    assert rows["Acted on"]["detail"] == "3 of 5"
    assert rows["Derailments (7d)"]["value"] == "1"
    assert rows["Derailments (7d)"]["delta"] == "-2 vs prior 7d"
    assert rows["Derailments (7d)"]["good"] is True


def test_attention_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path)
    con = sqlite3.connect(cfg.db_path)
    _create_notification_impacts(con)
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "attention")
    assert metrics(cfg) == []


def test_attention_metrics_no_table_returns_empty_list(tmp_path):
    cfg = _setup(tmp_path)
    metrics = _load_app_metrics_fn(cfg, "attention")
    assert metrics(cfg) == []


# --- calendar_reality --------------------------------------------------


def _create_calendar_reality_blocks(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE calendar_reality_blocks ("
        "event_id TEXT PRIMARY KEY, start_at TEXT NOT NULL, "
        "reality_label TEXT NOT NULL)"
    )


def test_calendar_reality_metrics(tmp_path):
    cfg = _setup(tmp_path)
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    _create_calendar_reality_blocks(con)
    labels = ["focused", "focused", "fragmented", "fragmented", "fragmented", "calendar_only"]
    for i, label in enumerate(labels):
        con.execute(
            "INSERT INTO calendar_reality_blocks(event_id, start_at, reality_label) "
            "VALUES (?, ?, ?)",
            (f"e{i}", _iso(now - timedelta(days=i)), label),
        )
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "calendar_reality")
    rows = _by_label(metrics(cfg))
    assert rows["Focused blocks (14d)"]["value"] == "2"
    assert rows["Fragmented blocks (14d)"]["value"] == "3"


def test_calendar_reality_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path)
    con = sqlite3.connect(cfg.db_path)
    _create_calendar_reality_blocks(con)
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "calendar_reality")
    assert metrics(cfg) == []


# --- subscriptions -------------------------------------------------------


def _create_subscription_entities(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE subscription_entities ("
        "subscription_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active', "
        "monthly_avg_amount REAL, latest_amount REAL)"
    )


def test_subscriptions_metrics(tmp_path):
    cfg = _setup(tmp_path)
    con = sqlite3.connect(cfg.db_path)
    _create_subscription_entities(con)
    con.execute(
        "INSERT INTO subscription_entities(subscription_id, status, monthly_avg_amount, latest_amount) "
        "VALUES ('s1', 'active', 10, NULL)"
    )
    con.execute(
        "INSERT INTO subscription_entities(subscription_id, status, monthly_avg_amount, latest_amount) "
        "VALUES ('s2', 'active', 20, NULL)"
    )
    # Canceled -- still counted in the monthly total (matches the app's own
    # overview_counts query, which sums across every row), just not "active".
    con.execute(
        "INSERT INTO subscription_entities(subscription_id, status, monthly_avg_amount, latest_amount) "
        "VALUES ('s3', 'canceled', NULL, 5)"
    )
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "subscriptions")
    rows = _by_label(metrics(cfg))
    assert rows["Active subscriptions"]["value"] == "2"
    assert rows["Monthly total"]["value"] == "$35.00"
    assert rows["Monthly total"]["sensitive"] is True


def test_subscriptions_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path)
    con = sqlite3.connect(cfg.db_path)
    _create_subscription_entities(con)
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "subscriptions")
    assert metrics(cfg) == []


# --- places ----------------------------------------------------------------


def _create_location_tables(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE location_points (id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, "
        "latitude REAL NOT NULL, longitude REAL NOT NULL, accuracy REAL)"
    )
    con.execute(
        "CREATE TABLE geocoded_locations (recorded_at TEXT PRIMARY KEY, "
        "formatted_address TEXT, place_id TEXT)"
    )


def test_places_metrics(tmp_path):
    cfg = _setup(tmp_path)
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    _create_location_tables(con)
    points = [
        ("p1", now - timedelta(hours=1), "place-a"),
        ("p2", now - timedelta(hours=2), "place-b"),
        ("p3", now - timedelta(hours=30), "place-c"),  # outside 24h, inside 30d
    ]
    for pid, ts, place in points:
        ts_iso = _iso(ts)
        con.execute(
            "INSERT INTO location_points(id, recorded_at, latitude, longitude) "
            "VALUES (?, ?, 1.0, 2.0)",
            (pid, ts_iso),
        )
        con.execute(
            "INSERT INTO geocoded_locations(recorded_at, formatted_address, place_id) "
            "VALUES (?, ?, ?)",
            (ts_iso, place, place),
        )
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "places")
    rows = _by_label(metrics(cfg))
    assert rows["GPS points (24h)"]["value"] == "2"
    assert rows["Places visited (30d)"]["value"] == "3"


def test_places_metrics_empty_tables(tmp_path):
    cfg = _setup(tmp_path)
    con = sqlite3.connect(cfg.db_path)
    _create_location_tables(con)
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "places")
    rows = _by_label(metrics(cfg))
    assert rows["GPS points (24h)"]["value"] == "0"


def test_places_metrics_no_table_returns_empty_list(tmp_path):
    cfg = _setup(tmp_path)
    metrics = _load_app_metrics_fn(cfg, "places")
    assert metrics(cfg) == []


# --- finance (app) -----------------------------------------------------


def _create_finance_daily_cashflow(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE finance_daily_cashflow (date TEXT NOT NULL, owner TEXT NOT NULL, "
        "income REAL NOT NULL DEFAULT 0, spending REAL NOT NULL DEFAULT 0, "
        "net REAL NOT NULL DEFAULT 0)"
    )


def test_finance_app_metrics(tmp_path):
    cfg = _setup(tmp_path)
    today = date.today()
    con = sqlite3.connect(cfg.db_path)
    _create_finance_daily_cashflow(con)
    # Within the last 7 days: net -100 today, +50 3 days ago -> cashflow(7d) = -50.
    con.execute(
        "INSERT INTO finance_daily_cashflow(date, owner, income, spending, net) "
        "VALUES (?, 'all', 0, 100, -100)",
        (today.isoformat(),),
    )
    con.execute(
        "INSERT INTO finance_daily_cashflow(date, owner, income, spending, net) "
        "VALUES (?, 'all', 150, 100, 50)",
        ((today - timedelta(days=3)).isoformat(),),
    )
    # Earlier this month (>7d ago, but still this month) -- counts toward
    # spend-this-month but not cashflow(7d).
    month_start = today.replace(day=1)
    if month_start < today - timedelta(days=8):
        earlier = today - timedelta(days=8)
        con.execute(
            "INSERT INTO finance_daily_cashflow(date, owner, income, spending, net) "
            "VALUES (?, 'all', 0, 40, -40)",
            (earlier.isoformat(),),
        )
        expected_spend = 100 + 100 + 40
    else:
        expected_spend = 100 + 100
    # A per-owner row that must be ignored in favor of the 'all' rollup.
    con.execute(
        "INSERT INTO finance_daily_cashflow(date, owner, income, spending, net) "
        "VALUES (?, 'self', 0, 5, -5)",
        (today.isoformat(),),
    )
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "finance")
    rows = _by_label(metrics(cfg))
    assert rows["Cashflow (7d)"]["value"] == "-$50"
    assert rows["Cashflow (7d)"]["sensitive"] is True
    assert rows["Cashflow (7d)"]["good"] is False
    assert rows["Spend this month"]["value"] == f"${expected_spend:,}"
    assert rows["Spend this month"]["sensitive"] is True


def test_finance_app_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path)
    con = sqlite3.connect(cfg.db_path)
    _create_finance_daily_cashflow(con)
    con.commit()
    con.close()

    metrics = _load_app_metrics_fn(cfg, "finance")
    assert metrics(cfg) == []


def test_finance_app_metrics_no_table_returns_empty_list(tmp_path):
    cfg = _setup(tmp_path)
    metrics = _load_app_metrics_fn(cfg, "finance")
    assert metrics(cfg) == []
