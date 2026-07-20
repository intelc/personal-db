"""Unit tests for the `metrics(cfg) -> list[dict]` dashboard-tile contract
implemented by a subset of bundled tracker templates.

Each installs the real tracker (via the CLI, so schema.sql runs exactly as
it does in production), seeds a handful of rows relative to "now" so the
tests stay valid regardless of when they run, loads the *installed* copy of
visualizations.py (mirroring personal_db.services.ui.viz._load_module), and
asserts on the returned metrics.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sqlite3
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_db.core.config import Config


def _setup(tmp_path: Path, *trackers: str) -> Config:
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True, capture_output=True,
    )
    for t in trackers:
        subprocess.run(
            [sys.executable, "-m", "personal_db.cli.main", "--root", str(root),
             "tracker", "install", t],
            check=True, capture_output=True,
        )
    return Config(root=root)


def _load_metrics_fn(cfg: Config, tracker: str):
    path = cfg.trackers_dir / tracker / "visualizations.py"
    modname = f"_test_metrics_{tracker}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod.metrics


def _by_label(rows: list[dict]) -> dict[str, dict]:
    return {r["label"]: r for r in rows}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# --- screen_time -------------------------------------------------------


def test_screen_time_metrics(tmp_path):
    cfg = _setup(tmp_path, "screen_time")
    now = datetime.now(UTC)
    today_start = now - timedelta(hours=1)
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO screen_time_app_usage(bundle_id, start_at, end_at, seconds) "
        "VALUES (?, ?, ?, ?)",
        ("com.apple.Safari", _iso(today_start), _iso(now), 3600),
    )
    con.execute(
        "INSERT INTO screen_time_app_usage(bundle_id, start_at, end_at, seconds) "
        "VALUES (?, ?, ?, ?)",
        ("com.apple.Terminal", _iso(today_start), _iso(now), 600),
    )
    con.execute(
        "INSERT INTO screen_time_app_names(bundle_id, app_name, resolved_at) VALUES (?, ?, ?)",
        ("com.apple.Safari", "Safari", _iso(now)),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "screen_time")
    rows = metrics(cfg)
    assert rows, "expected at least one metric"
    by_label = _by_label(rows)
    assert by_label["Mac today"]["value"] == "1.2h"
    assert by_label["Top app today"]["value"] == "Safari"
    assert by_label["Top app today"]["detail"] == "1.0h"


def test_screen_time_metrics_empty_table_returns_list_without_raising(tmp_path):
    cfg = _setup(tmp_path, "screen_time")
    metrics = _load_metrics_fn(cfg, "screen_time")
    rows = metrics(cfg)
    assert isinstance(rows, list)
    if rows:
        assert _by_label(rows)["Mac today"]["value"] == "0.0h"


# --- github_commits ------------------------------------------------------


def test_github_commits_metrics_percent_delta(tmp_path):
    cfg = _setup(tmp_path, "github_commits")
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    # This week: 10 commits across 2 repos. Previous week: 5 commits (baseline
    # >= 5, so the metric should render a percent delta).
    for i in range(10):
        con.execute(
            "INSERT INTO github_commits(sha, repo, committed_at, message) VALUES (?, ?, ?, ?)",
            (f"this{i}", "me/repo-a" if i % 2 else "me/repo-b",
             _iso(now - timedelta(days=1, hours=i)), "commit"),
        )
    for i in range(5):
        con.execute(
            "INSERT INTO github_commits(sha, repo, committed_at, message) VALUES (?, ?, ?, ?)",
            (f"prev{i}", "me/repo-a", _iso(now - timedelta(days=10, hours=i)), "commit"),
        )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "github_commits")
    rows = _by_label(metrics(cfg))
    assert rows["Commits this week"]["value"] == "10"
    assert rows["Commits this week"]["delta"] == "+100% vs last week"
    assert rows["Commits this week"]["good"] is True
    assert rows["Active repos (30d)"]["value"] == "2"


def test_github_commits_metrics_small_baseline_uses_absolute_delta(tmp_path):
    cfg = _setup(tmp_path, "github_commits")
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    for i in range(8):
        con.execute(
            "INSERT INTO github_commits(sha, repo, committed_at, message) VALUES (?, ?, ?, ?)",
            (f"this{i}", "me/repo-a", _iso(now - timedelta(days=1, hours=i)), "commit"),
        )
    con.execute(
        "INSERT INTO github_commits(sha, repo, committed_at, message) VALUES (?, ?, ?, ?)",
        ("prev0", "me/repo-a", _iso(now - timedelta(days=10)), "commit"),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "github_commits")
    rows = _by_label(metrics(cfg))
    # prev_week (1) < 5 -> absolute-count delta, not a wild percentage.
    assert rows["Commits this week"]["delta"] == "+7 vs last week"


def test_github_commits_metrics_empty_returns_list(tmp_path):
    cfg = _setup(tmp_path, "github_commits")
    metrics = _load_metrics_fn(cfg, "github_commits")
    rows = _by_label(metrics(cfg))
    assert rows["Commits this week"]["value"] == "0"
    assert rows["Commits this week"]["delta"] is None
    assert rows["Active repos (30d)"]["value"] == "0"


# --- imessage --------------------------------------------------------------


def test_imessage_metrics(tmp_path):
    cfg = _setup(tmp_path, "imessage")
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    # 3 messages today (2 inbound from the same handle).
    con.execute(
        "INSERT INTO imessage_messages(handle, text, is_from_me, sent_at) VALUES (?, ?, ?, ?)",
        ("+15551234567", "hi", 0, _iso(now - timedelta(minutes=5))),
    )
    con.execute(
        "INSERT INTO imessage_messages(handle, text, is_from_me, sent_at) VALUES (?, ?, ?, ?)",
        ("+15551234567", "there", 0, _iso(now - timedelta(minutes=3))),
    )
    con.execute(
        "INSERT INTO imessage_messages(handle, text, is_from_me, sent_at) VALUES (?, ?, ?, ?)",
        ("+15551234567", "reply", 1, _iso(now - timedelta(minutes=1))),
    )
    # 10 more messages spread through the rest of this week (for the 7d bucket).
    for i in range(10):
        con.execute(
            "INSERT INTO imessage_messages(handle, text, is_from_me, sent_at) VALUES (?, ?, ?, ?)",
            ("+15551234567", f"msg{i}", 0, _iso(now - timedelta(days=2, hours=i))),
        )
    # 5 messages in the prior 7-day window.
    for i in range(5):
        con.execute(
            "INSERT INTO imessage_messages(handle, text, is_from_me, sent_at) VALUES (?, ?, ?, ?)",
            ("+15559999999", f"old{i}", 0, _iso(now - timedelta(days=10, hours=i))),
        )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "imessage")
    rows = _by_label(metrics(cfg))
    assert rows["Messages today"]["value"] == "3"
    assert rows["Messages (7d)"]["value"] == "13"
    assert rows["Messages (7d)"]["good"] is None
    assert rows["Top contact (30d)"]["value"] == "+15551234567"
    assert rows["Top contact (30d)"]["detail"] == "12 inbound"


def test_imessage_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path, "imessage")
    metrics = _load_metrics_fn(cfg, "imessage")
    rows = _by_label(metrics(cfg))
    assert rows["Messages today"]["value"] == "0"
    assert "Top contact (30d)" not in rows


# --- calendar ----------------------------------------------------------


def test_calendar_metrics(tmp_path):
    cfg = _setup(tmp_path, "calendar")
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    # A meeting earlier today (already passed) + one all-day entry today
    # (should be excluded from the "meetings today" count).
    con.execute(
        "INSERT INTO calendar_events(event_id, source, title, start_at, end_at, "
        "all_day, imported_at, deleted_at) VALUES (?, 'test', ?, ?, ?, 0, ?, NULL)",
        ("evt-1", "Standup", _iso(now - timedelta(hours=2)),
         _iso(now - timedelta(hours=1, minutes=30)), _iso(now)),
    )
    con.execute(
        "INSERT INTO calendar_events(event_id, source, title, start_at, end_at, "
        "all_day, imported_at, deleted_at) VALUES (?, 'test', ?, ?, ?, 1, ?, NULL)",
        ("evt-holiday", "Some Holiday", _iso(now.replace(hour=0, minute=0, second=0)),
         _iso(now.replace(hour=23, minute=59, second=59)), _iso(now)),
    )
    # A soft-deleted event today — must not count.
    con.execute(
        "INSERT INTO calendar_events(event_id, source, title, start_at, end_at, "
        "all_day, imported_at, deleted_at) VALUES (?, 'test', ?, ?, ?, 0, ?, ?)",
        ("evt-deleted", "Cancelled", _iso(now - timedelta(hours=1)),
         _iso(now - timedelta(minutes=30)), _iso(now), _iso(now)),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "calendar")
    rows = _by_label(metrics(cfg))
    assert rows["Meetings today"]["value"] == "1"
    assert rows["Next event"]["value"] == "none left today"
    assert rows["Meeting hours (7d)"]["value"] == "0.5h"


def test_calendar_metrics_upcoming_event_today(tmp_path):
    cfg = _setup(tmp_path, "calendar")
    now = datetime.now(UTC)
    future = now + timedelta(hours=1)
    if future.astimezone().date() != now.astimezone().date():
        # Avoid a flaky test right around local midnight ("today" in the
        # metric is the LOCAL day, so the guard must compare local dates —
        # comparing UTC dates left an 11pm-midnight window where the event
        # landed on local-tomorrow and "Next event" was legitimately empty).
        future = now + timedelta(minutes=1)
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO calendar_events(event_id, source, title, start_at, end_at, "
        "all_day, imported_at, deleted_at) VALUES (?, 'test', ?, ?, ?, 0, ?, NULL)",
        ("evt-future", "1:1 with Sam", _iso(future), _iso(future + timedelta(minutes=30)), _iso(now)),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "calendar")
    rows = _by_label(metrics(cfg))
    assert rows["Next event"]["detail"] == "1:1 with Sam"
    assert rows["Next event"]["value"] != "none left today"


def test_calendar_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path, "calendar")
    metrics = _load_metrics_fn(cfg, "calendar")
    rows = _by_label(metrics(cfg))
    assert rows["Meetings today"]["value"] == "0"
    assert rows["Next event"]["value"] == "none left today"
    assert rows["Meeting hours (7d)"]["value"] == "0.0h"


# --- chrome_history ------------------------------------------------------


def test_chrome_history_metrics(tmp_path):
    cfg = _setup(tmp_path, "chrome_history")
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO chrome_visits(visit_id, profile, url, domain, visited_at, duration_seconds) "
        "VALUES (1, 'Default', 'https://example.com', 'example.com', ?, 3600)",
        (_iso(now - timedelta(minutes=30)),),
    )
    con.execute(
        "INSERT INTO chrome_visits(visit_id, profile, url, domain, visited_at, duration_seconds) "
        "VALUES (2, 'Default', 'https://news.com', 'news.com', ?, 600)",
        (_iso(now - timedelta(minutes=10)),),
    )
    # 30d-ago baseline row so the average-vs-today delta has a denominator.
    con.execute(
        "INSERT INTO chrome_visits(visit_id, profile, url, domain, visited_at, duration_seconds) "
        "VALUES (3, 'Default', 'https://example.com', 'example.com', ?, 36000)",
        (_iso(now - timedelta(days=15)),),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "chrome_history")
    rows = _by_label(metrics(cfg))
    assert rows["Browsing today"]["value"] == "1.2h"  # (3600 + 600) / 3600
    assert rows["Top domain today"]["value"] == "example.com"
    assert rows["Top domain today"]["detail"] == "1.0h"


def test_chrome_history_metrics_strips_leading_www(tmp_path):
    """Display-only: a leading "www." on the top domain is stripped so the
    tile shows "youtube.com" rather than "www.youtube.com" (avoidable extra
    length that pushes long domains further into overflow territory)."""
    cfg = _setup(tmp_path, "chrome_history")
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO chrome_visits(visit_id, profile, url, domain, visited_at, duration_seconds) "
        "VALUES (1, 'Default', 'https://www.youtube.com', 'www.youtube.com', ?, 3600)",
        (_iso(now - timedelta(minutes=30)),),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "chrome_history")
    rows = _by_label(metrics(cfg))
    assert rows["Top domain today"]["value"] == "youtube.com"


def test_chrome_history_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path, "chrome_history")
    metrics = _load_metrics_fn(cfg, "chrome_history")
    rows = _by_label(metrics(cfg))
    assert rows["Browsing today"]["value"] == "0.0h"
    assert "Top domain today" not in rows


# --- code_agent_activity -------------------------------------------------


def test_code_agent_activity_metrics(tmp_path):
    cfg = _setup(tmp_path, "code_agent_activity")
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO code_agent_intervals(agent, session_id, start_ts, end_ts, state, duration_seconds) "
        "VALUES ('claude_code', 'sess-1', ?, ?, 'agent_running', 1800)",
        (_iso(now - timedelta(minutes=30)), _iso(now)),
    )
    con.execute(
        "INSERT INTO code_agent_events(agent, session_id, timestamp, event_type) "
        "VALUES ('claude_code', 'sess-1', ?, 'prompt_submitted')",
        (_iso(now - timedelta(minutes=20)),),
    )
    con.execute(
        "INSERT INTO code_agent_events(agent, session_id, timestamp, event_type) "
        "VALUES ('claude_code', 'sess-1', ?, 'prompt_submitted')",
        (_iso(now - timedelta(minutes=10)),),
    )
    con.execute(
        "INSERT INTO code_agent_sessions(agent, session_id, started_at, last_msg_at, "
        "message_count, user_msg_count, assistant_msg_count) "
        "VALUES ('claude_code', 'sess-1', ?, ?, 4, 2, 2)",
        (_iso(now - timedelta(minutes=30)), _iso(now)),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "code_agent_activity")
    rows = _by_label(metrics(cfg))
    assert rows["Agent running today"]["value"] == "0.5h"
    assert rows["Prompts today"]["value"] == "2"
    assert rows["Sessions today"]["value"] == "1"


def test_code_agent_activity_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path, "code_agent_activity")
    metrics = _load_metrics_fn(cfg, "code_agent_activity")
    rows = _by_label(metrics(cfg))
    assert rows["Agent running today"]["value"] == "0.0h"
    assert rows["Prompts today"]["value"] == "0"
    assert rows["Sessions today"]["value"] == "0"


# --- whoop -----------------------------------------------------------------


def test_whoop_metrics_good_and_bad_zones(tmp_path):
    cfg = _setup(tmp_path, "whoop")
    now = datetime.now(UTC)
    stale = now - timedelta(days=10)
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO whoop_recovery(cycle_id, start, recovery_score) VALUES (?, ?, ?)",
        ("cyc-1", _iso(stale), 20),
    )
    con.execute(
        "INSERT INTO whoop_sleep(id, start, nap, sleep_efficiency_pct) VALUES (?, ?, 0, ?)",
        ("sleep-1", _iso(stale), 88.4),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "whoop")
    rows = _by_label(metrics(cfg))
    assert rows["Recovery"]["value"] == "20"
    assert rows["Recovery"]["good"] is False
    assert rows["Recovery"]["detail"] == f"as of {stale.astimezone().date().isoformat()}"
    assert rows["Sleep efficiency"]["value"] == "88%"


def test_whoop_metrics_good_recovery_zone(tmp_path):
    cfg = _setup(tmp_path, "whoop")
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO whoop_recovery(cycle_id, start, recovery_score) VALUES (?, ?, ?)",
        ("cyc-1", _iso(now), 80),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "whoop")
    rows = _by_label(metrics(cfg))
    assert rows["Recovery"]["good"] is True
    # Reading is from today (local) -> no "as of" staleness note.
    assert rows["Recovery"]["detail"] is None


def test_whoop_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path, "whoop")
    metrics = _load_metrics_fn(cfg, "whoop")
    assert metrics(cfg) == []


# --- oura --------------------------------------------------------------


def test_oura_metrics(tmp_path):
    cfg = _setup(tmp_path, "oura")
    stale_day = (datetime.now() - timedelta(days=300)).date().isoformat()
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO oura_daily_readiness(id, day, score) VALUES ('r1', ?, 55)",
        (stale_day,),
    )
    con.execute(
        "INSERT INTO oura_daily_sleep(id, day, score) VALUES ('s1', ?, 85)",
        (stale_day,),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "oura")
    rows = _by_label(metrics(cfg))
    assert rows["Readiness"]["value"] == "55"
    assert rows["Readiness"]["good"] is False  # < 60
    assert rows["Readiness"]["detail"] == f"as of {stale_day}"
    assert rows["Sleep score"]["value"] == "85"
    assert rows["Sleep score"]["good"] is True  # >= 80


def test_oura_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path, "oura")
    metrics = _load_metrics_fn(cfg, "oura")
    assert metrics(cfg) == []


# --- finance -------------------------------------------------------------


def test_finance_metrics(tmp_path):
    cfg = _setup(tmp_path, "finance")
    today = datetime.now().date()
    prior = today - timedelta(days=30)
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO finance_daily_net_worth(date, owner, cash, investments, "
        "credit_card_debt, other, assets, debts, net_worth) "
        "VALUES (?, 'all', 19000, 190000, 8000, 0, 209000, 8000, 201000)",
        (today.isoformat(),),
    )
    con.execute(
        "INSERT INTO finance_daily_net_worth(date, owner, cash, investments, "
        "credit_card_debt, other, assets, debts, net_worth) "
        "VALUES (?, 'all', 15000, 180000, 8000, 0, 195000, 8000, 187000)",
        (prior.isoformat(),),
    )
    # A per-owner row that should be ignored in favor of the 'all' rollup.
    con.execute(
        "INSERT INTO finance_daily_net_worth(date, owner, cash, investments, "
        "credit_card_debt, other, assets, debts, net_worth) "
        "VALUES (?, 'self', 1, 1, 0, 0, 2, 0, 2)",
        (today.isoformat(),),
    )
    con.commit()
    con.close()

    metrics = _load_metrics_fn(cfg, "finance")
    rows = _by_label(metrics(cfg))
    assert rows["Net worth"]["value"] == "$201k"
    assert rows["Net worth"]["good"] is True
    assert "vs 30d ago" in rows["Net worth"]["delta"]
    assert rows["Cash"]["value"] == "$19k"
    assert rows["Net worth"]["detail"] is None  # dated today -> no staleness note


def test_finance_metrics_empty_table(tmp_path):
    cfg = _setup(tmp_path, "finance")
    metrics = _load_metrics_fn(cfg, "finance")
    assert metrics(cfg) == []
