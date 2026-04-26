"""Visualizations for github_commits."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import calendar_grid, horizontal_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_contribution_calendar(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
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
        return '<p class="meta">github_commits not synced yet</p>'
    finally:
        con.close()

    by_day: dict[date, float] = {}
    for d_str, n in rows:
        try:
            by_day[date.fromisoformat(d_str)] = float(n)
        except (TypeError, ValueError):
            continue
    if not by_day:
        return '<p class="meta">no commits in the last 13 weeks</p>'

    # Green-ramp coloring (the iconic GitHub palette but desaturated to fit).
    def _color(v: float) -> str:
        if v <= 0:
            return "#eee"
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
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        rows = con.execute(
            "SELECT repo, count(*) AS n FROM github_commits "
            "WHERE committed_at >= ? GROUP BY repo ORDER BY n DESC LIMIT 15",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">github_commits not synced yet</p>'
    finally:
        con.close()
    items = [(repo, n) for repo, n in rows if n]
    return (
        '<p class="meta">last 30 days · top repos by commit count</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{int(v)}")
    )


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
