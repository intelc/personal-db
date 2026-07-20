"""Visualizations for github_commits."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import calendar_grid, horizontal_bars
from personal_db.viz_helpers import connect_db as _connect
from personal_db.viz_helpers import meta


def render_contribution_calendar(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return meta("no data")
    today = date.today()
    weeks = 13
    start = today - timedelta(days=weeks * 7 - 1)
    try:
        rows = con.execute(
            "SELECT date(committed_at, 'localtime') AS d, count(*) AS n "
            "FROM github_commits WHERE committed_at >= ? GROUP BY d",
            (start.isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        return meta("github_commits not synced yet")
    finally:
        con.close()

    by_day: dict[date, float] = {}
    for d_str, n in rows:
        try:
            by_day[date.fromisoformat(d_str)] = float(n)
        except (TypeError, ValueError):
            continue
    if not by_day:
        return meta("no commits in the last 13 weeks")

    # Green-ramp coloring (the iconic GitHub palette but desaturated to fit).
    def _color(v: float) -> str:
        if v <= 0:
            return "var(--chart-grid)"
        if v <= 1:
            return "#9be9a8"
        if v <= 3:
            return "#40c463"
        if v <= 6:
            return "#30a14e"
        return "#216e39"

    total = int(sum(by_day.values()))
    return (
        f'<p class="meta">{total} commits in the last {weeks} weeks · '
        "darker green = more commits</p>"
        + calendar_grid(by_day, end_date=today, weeks=weeks, color_fn=_color)
    )


def render_top_repos(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return meta("no data")
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        rows = con.execute(
            "SELECT repo, count(*) AS n FROM github_commits "
            "WHERE committed_at >= ? GROUP BY repo ORDER BY n DESC LIMIT 15",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return meta("github_commits not synced yet")
    finally:
        con.close()
    items = [(repo, n) for repo, n in rows if n]
    return (
        '<p class="meta">last 30 days · top repos by commit count</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{int(v)}")
    )


def metrics(cfg: Config) -> list[dict]:
    """Dashboard tile metrics: commits this week (vs previous week) and
    active repos in the last 30 days."""
    con = _connect(cfg)
    if not con:
        return []
    out: list[dict] = []
    try:
        row = con.execute(
            "SELECT "
            "  sum(CASE WHEN committed_at >= datetime('now', '-7 days') THEN 1 ELSE 0 END), "
            "  sum(CASE WHEN committed_at >= datetime('now', '-14 days') "
            "           AND committed_at < datetime('now', '-7 days') THEN 1 ELSE 0 END) "
            "FROM github_commits WHERE committed_at >= datetime('now', '-14 days')",
        ).fetchone()
        active_repos = con.execute(
            "SELECT count(DISTINCT repo) FROM github_commits "
            "WHERE committed_at >= datetime('now', '-30 days')"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    this_week, prev_week = (row or (0, 0))
    this_week = this_week or 0
    prev_week = prev_week or 0

    delta = None
    good = None
    if this_week != prev_week:
        good = True if this_week > prev_week else False
    # Percentages off a tiny baseline (e.g. 2 -> 78 = "+3800%") are more
    # confusing than useful, so fall back to an absolute-count delta below
    # a small-baseline threshold.
    if prev_week >= 5:
        pct = (this_week - prev_week) / prev_week * 100
        sign = "+" if pct >= 0 else ""
        delta = f"{sign}{pct:.0f}% vs last week"
    elif this_week != prev_week:
        diff = this_week - prev_week
        sign = "+" if diff >= 0 else ""
        delta = f"{sign}{int(diff)} vs last week"

    out.append(
        {
            "label": "Commits this week",
            "value": str(int(this_week)),
            "detail": None,
            "delta": delta,
            "good": good,
        }
    )
    out.append(
        {
            "label": "Active repos (30d)",
            "value": str(int(active_repos or 0)),
            "detail": None,
            "delta": None,
            "good": None,
        }
    )
    return out


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "contribution_calendar",
            "name": "Contribution Calendar",
            "description": "GitHub-style contribution grid: 13 weeks × 7 days.",
            "render": render_contribution_calendar,
        },
        {
            "slug": "top_repos_30d",
            "name": "Top Repos (30d)",
            "description": "Repositories with the most commits in the last 30 days.",
            "render": render_top_repos,
        },
    ]
