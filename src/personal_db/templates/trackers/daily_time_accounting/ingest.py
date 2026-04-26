"""Derived tracker: daily time accounting from sleep + workouts + screen_time."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path

import yaml

from personal_db.tracker import Tracker


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


def _load_categories(tracker_dir: Path) -> tuple[dict[str, str], str]:
    """Load app_categories.yaml. Returns (bundle_id -> category, default_category)."""
    p = tracker_dir / "app_categories.yaml"
    if not p.exists():
        return {}, "other_screen"
    cfg = yaml.safe_load(p.read_text()) or {}
    default = cfg.pop("default", "other_screen")
    inverted: dict[str, str] = {}
    for cat, bundles in cfg.items():
        if not isinstance(bundles, list):
            continue
        for b in bundles:
            inverted[b] = cat
    return inverted, default


def _table_exists(con, name: str) -> bool:
    r = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return r is not None


def sync(t: Tracker) -> None:
    tracker_dir = t.cfg.trackers_dir / "daily_time_accounting"
    bundle_to_cat, default_cat = _load_categories(tracker_dir)

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

    rows: list[dict] = []
    for day in _date_iter(start_date, today):
        # Per-date totals
        per_cat: dict[str, float] = defaultdict(float)

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
                cat = bundle_to_cat.get(r["bundle_id"], default_cat)
                per_cat[cat] += _hours_in_date(
                    _to_local(r["start_at"]), _to_local(r["end_at"]), day
                )

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
