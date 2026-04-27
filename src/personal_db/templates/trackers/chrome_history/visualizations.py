"""Visualizations for chrome_history."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import heatmap, horizontal_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_top_domains(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=30)).date().isoformat()
    try:
        rows = con.execute(
            "SELECT domain, sum(duration_seconds)/3600.0 AS hours "
            "FROM chrome_visits "
            "WHERE duration_seconds > 0 AND visited_at >= ? AND domain != '' "
            "GROUP BY domain ORDER BY hours DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">chrome_visits not synced yet</p>'
    finally:
        con.close()
    items = [(d, round(h, 1)) for d, h in rows if h]
    return (
        '<p class="meta">last 30 days · top 20 domains by total dwell time</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{v}h")
    )


def render_hourly_heatmap(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    try:
        rows = con.execute(
            "SELECT date(visited_at, 'localtime') AS d, "
            "       cast(strftime('%H', visited_at, 'localtime') AS INTEGER) AS h, "
            "       count(*) AS n "
            "FROM chrome_visits WHERE visited_at >= ? GROUP BY d, h",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">chrome_visits not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no visits in the last 7 days</p>'
    today = datetime.now().date()
    days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    by_day_hour: dict[tuple[str, int], int] = {(d, h): n for d, h, n in rows}
    grid = [[by_day_hour.get((d.isoformat(), h), 0) or None for h in range(24)] for d in days]
    row_labels = [d.strftime("%a %m-%d") for d in days]
    col_labels = [f"{h:02d}" for h in range(24)]
    return (
        '<p class="meta">visits per hour, last 7 days · darker = more</p>'
        + heatmap(grid, row_labels, col_labels)
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "top_domains_30d",
            "name": "Top Domains (30d)",
            "description": "Top 20 domains by total dwell time over the last 30 days.",
            "render": render_top_domains,
        },
        {
            "slug": "hourly_heatmap_7d",
            "name": "Hourly Heatmap (7d)",
            "description": "Browsing intensity by day-of-week × hour-of-day.",
            "render": render_hourly_heatmap,
        },
    ]
