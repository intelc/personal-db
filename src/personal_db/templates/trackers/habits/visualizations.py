"""Visualizations for the habits tracker (manual daily logging)."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape

from personal_db.config import Config
from personal_db.ui.charts import calendar_grid


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def _logs_by_habit(cfg: Config, days: int) -> dict[str, set[date]]:
    """For each habit name, the set of local-dates it was logged on."""
    con = _connect(cfg)
    if not con:
        return {}
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        rows = con.execute(
            "SELECT name, date(ts, 'localtime') FROM habits WHERE ts >= ? AND name IS NOT NULL",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()
    out: dict[str, set[date]] = defaultdict(set)
    for name, d_str in rows:
        try:
            out[name].add(date.fromisoformat(d_str))
        except (TypeError, ValueError):
            continue
    return dict(out)


def render_streaks(cfg: Config) -> str:
    """For each habit, show current and longest streak (consecutive days logged)."""
    by_habit = _logs_by_habit(cfg, days=365)
    if not by_habit:
        return '<p class="meta">no habits logged yet</p>'
    today = date.today()

    def _longest_and_current(dates: set[date]) -> tuple[int, int]:
        if not dates:
            return 0, 0
        sorted_d = sorted(dates)
        longest = best = 1
        for prev, cur in zip(sorted_d, sorted_d[1:], strict=False):
            if (cur - prev).days == 1:
                best += 1
                longest = max(longest, best)
            else:
                best = 1
        # Current streak: walk back from today
        cur_streak = 0
        d = today
        while d in dates:
            cur_streak += 1
            d -= timedelta(days=1)
        return longest, cur_streak

    rows = []
    for name in sorted(by_habit.keys()):
        longest, current = _longest_and_current(by_habit[name])
        rows.append(
            f"<tr><td><strong>{escape(name)}</strong></td>"
            f"<td>{current} day{'s' if current != 1 else ''}</td>"
            f"<td>{longest} day{'s' if longest != 1 else ''}</td>"
            f"<td class=\"meta\">{len(by_habit[name])} total logs</td></tr>"
        )
    return (
        '<table class="health">'
        "<thead><tr><th>habit</th><th>current streak</th>"
        "<th>longest streak</th><th>total</th></tr></thead>"
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def render_calendar(cfg: Config) -> str:
    """30-day calendar: a cell is darker the more habits were logged that day."""
    by_habit = _logs_by_habit(cfg, days=90)
    if not by_habit:
        return '<p class="meta">no habits logged yet</p>'
    counts: dict[date, float] = defaultdict(float)
    for dates in by_habit.values():
        for d in dates:
            counts[d] += 1
    today = date.today()
    return (
        '<p class="meta">last 13 weeks · darker cells = more habits logged that day</p>'
        + calendar_grid(dict(counts), end_date=today, weeks=13)
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "streaks",
            "name": "Habit Streaks",
            "description": "Current and longest consecutive-day streak per habit.",
            "render": render_streaks,
        },
        {
            "slug": "calendar",
            "name": "Habit Calendar (13w)",
            "description": "13-week grid colored by daily habit count.",
            "render": render_calendar,
        },
    ]
