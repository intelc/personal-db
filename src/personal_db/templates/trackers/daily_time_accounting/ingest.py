"""Derived tracker: daily time accounting from sleep + workouts + screen_time + chrome_visits."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date as date_t
from datetime import datetime, time, timedelta
from pathlib import Path

import yaml

from personal_db.tracker import Tracker

_CHROME_BUNDLE = "com.google.Chrome"


def _local_tz():
    """User's local timezone (system default)."""
    return datetime.now().astimezone().tzinfo


def _to_local(iso_str: str):
    """Parse an ISO-8601 string (Z-suffixed or with offset) and return local datetime."""
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(_local_tz())


def _date_iter(start_date, end_date):
    cur = start_date
    while cur <= end_date:
        yield cur
        cur += timedelta(days=1)


def _hours_in_date(start_dt, end_dt, day_date) -> float:
    """How many hours of [start_dt, end_dt] fall within the local date `day_date`?"""
    tz = _local_tz()
    day_start = datetime.combine(day_date, time.min, tzinfo=tz)
    day_end = datetime.combine(day_date + timedelta(days=1), time.min, tzinfo=tz)
    overlap_start = max(start_dt, day_start)
    overlap_end = min(end_dt, day_end)
    if overlap_end <= overlap_start:
        return 0.0
    return (overlap_end - overlap_start).total_seconds() / 3600.0


def _load_categories(
    tracker_dir: Path,
) -> tuple[dict[str, str], str, list[tuple[str, str]], str]:
    """Load app_categories.yaml.

    Returns:
        bundle_to_cat: bundle_id -> category
        default_app_cat: fallback category for unmapped bundles
        domain_rules: [(domain, category), ...] sorted by domain length desc
                       (longest match wins; suffix-match on hostname)
        default_domain_cat: fallback for chrome URLs whose domain isn't mapped
    """
    p = tracker_dir / "app_categories.yaml"
    if not p.exists():
        return {}, "other_screen", [], "other_screen"
    cfg = yaml.safe_load(p.read_text()) or {}
    default_app = cfg.pop("default", "other_screen")
    chrome_cfg = cfg.pop("chrome_domains", {}) or {}
    default_domain = chrome_cfg.pop("default", default_app)

    bundle_to_cat: dict[str, str] = {}
    for cat, bundles in cfg.items():
        if not isinstance(bundles, list):
            continue
        for b in bundles:
            bundle_to_cat[b] = cat

    domain_pairs: list[tuple[str, str]] = []
    for cat, domains in chrome_cfg.items():
        if not isinstance(domains, list):
            continue
        for d in domains:
            domain_pairs.append((str(d).lower(), cat))
    # Longest match wins (so "mail.google.com" beats "google.com")
    domain_pairs.sort(key=lambda x: len(x[0]), reverse=True)

    return bundle_to_cat, default_app, domain_pairs, default_domain


def _categorize_domain(
    hostname: str | None, domain_rules: list[tuple[str, str]], default_cat: str
) -> str:
    if not hostname:
        return default_cat
    h = hostname.lower()
    for domain, cat in domain_rules:
        if h == domain or h.endswith("." + domain):
            return cat
    return default_cat


def _chrome_dwell_by_day(
    con, domain_rules: list[tuple[str, str]], default_cat: str
) -> dict[date_t, dict[str, float]]:
    """Aggregate chrome_visits dwell time per (local_date, category).

    Visits with non-positive duration are skipped — they're either redirects
    or pages closed before any measurable dwell, and including them as
    visit-count would dilute the high-signal entries.
    """
    out: dict[date_t, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if not _table_exists(con, "chrome_visits"):
        return out
    for r in con.execute(
        "SELECT domain, visited_at, duration_seconds FROM chrome_visits "
        "WHERE duration_seconds > 0"
    ):
        try:
            d = _to_local(r["visited_at"]).date()
        except (TypeError, ValueError):
            continue
        cat = _categorize_domain(r["domain"], domain_rules, default_cat)
        out[d][cat] += r["duration_seconds"] / 3600.0
    return out


def _table_exists(con, name: str) -> bool:
    r = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return r is not None


def sync(t: Tracker) -> None:
    tracker_dir = t.cfg.trackers_dir / "daily_time_accounting"
    bundle_to_cat, default_cat, domain_rules, default_domain_cat = _load_categories(tracker_dir)

    today = datetime.now(_local_tz()).date()
    cursor = t.cursor.get()
    if cursor:
        # Recompute the last 2 days (in case yesterday's data was incomplete)
        start_date = max(
            datetime.fromisoformat(cursor).date() - timedelta(days=1),
            today - timedelta(days=2),
        )
    else:
        start_date = today - timedelta(days=90)

    con = sqlite3.connect(t.cfg.db_path)
    con.row_factory = sqlite3.Row

    chrome_dwell = _chrome_dwell_by_day(con, domain_rules, default_domain_cat)

    rows: list[dict] = []
    for day in _date_iter(start_date, today):
        # Per-date totals
        per_cat: dict[str, float] = defaultdict(float)
        chrome_screen_hours = 0.0  # bucketed separately for URL redistribution

        # Sleep (whoop_sleep)
        if _table_exists(con, "whoop_sleep"):
            for r in con.execute(
                "SELECT start, end FROM whoop_sleep WHERE start IS NOT NULL AND end IS NOT NULL"
            ):
                per_cat["sleep"] += _hours_in_date(_to_local(r["start"]), _to_local(r["end"]), day)

        # Workouts (whoop_workouts)
        if _table_exists(con, "whoop_workouts"):
            for r in con.execute(
                "SELECT start, end FROM whoop_workouts WHERE start IS NOT NULL AND end IS NOT NULL"
            ):
                per_cat["workout"] += _hours_in_date(
                    _to_local(r["start"]), _to_local(r["end"]), day
                )

        # Screen time
        if _table_exists(con, "screen_time_app_usage"):
            for r in con.execute(
                "SELECT bundle_id, start_at, end_at FROM screen_time_app_usage "
                "WHERE start_at IS NOT NULL AND end_at IS NOT NULL"
            ):
                hours = _hours_in_date(
                    _to_local(r["start_at"]), _to_local(r["end_at"]), day
                )
                if r["bundle_id"] == _CHROME_BUNDLE:
                    chrome_screen_hours += hours
                else:
                    cat = bundle_to_cat.get(r["bundle_id"], default_cat)
                    per_cat[cat] += hours

        # Redistribute Chrome screen-time across URL categories using visit dwell ratios.
        # screen_time is authoritative for *total* Chrome time; chrome_visits supplies the split.
        if chrome_screen_hours > 0:
            day_dwell = chrome_dwell.get(day, {})
            total_dwell = sum(day_dwell.values())
            if total_dwell > 0:
                for cat, dwell in day_dwell.items():
                    per_cat[cat] += chrome_screen_hours * (dwell / total_dwell)
            else:
                # No usable visits — fall back to Chrome's app-level category
                per_cat[bundle_to_cat.get(_CHROME_BUNDLE, default_cat)] += chrome_screen_hours

        # Compute residual
        accounted = sum(per_cat.values())
        per_cat["_unaccounted"] = 24.0 - accounted

        date_str = day.isoformat()
        for cat, hrs in per_cat.items():
            rows.append({"date": date_str, "category": cat, "hours": round(hrs, 4)})

    con.close()
    if rows:
        t.upsert("daily_time_accounting", rows, key=["date", "category"])
        t.cursor.set(today.isoformat())

    t.log.info(
        "daily_time_accounting: computed %d rows for %d days",
        len(rows),
        (today - start_date).days + 1,
    )


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    # Force full recompute by clearing cursor
    t.cursor.set("")
    sync(t)
