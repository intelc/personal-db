"""Visualizations for screen_time_app_usage."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from personal_db.app_names import resolve_app_name
from personal_db.config import Config
from personal_db.ui.charts import heatmap, horizontal_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_top_apps(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        rows = con.execute(
            "SELECT bundle_id, sum(seconds)/3600.0 AS hours "
            "FROM screen_time_app_usage WHERE start_at >= ? "
            "GROUP BY bundle_id ORDER BY hours DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">screen_time_app_usage not synced yet</p>'
    finally:
        con.close()
    items = []
    for bundle_id, hours in rows:
        if not hours:
            continue
        try:
            name = resolve_app_name(bundle_id) or bundle_id
        except Exception:  # noqa: BLE001 — resolver shouldn't break the viz
            name = bundle_id
        items.append((name, round(hours, 1)))
    return (
        '<p class="meta">last 30 days · top 20 apps by total foreground time</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{v}h")
    )


def render_hourly_heatmap(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    try:
        rows = con.execute(
            "SELECT date(start_at, 'localtime') AS d, "
            "       cast(strftime('%H', start_at, 'localtime') AS INTEGER) AS h, "
            "       sum(seconds)/60.0 AS minutes "
            "FROM screen_time_app_usage WHERE start_at >= ? GROUP BY d, h",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">screen_time_app_usage not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no usage in the last 7 days</p>'
    today = datetime.now().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    by_dh: dict[tuple[str, int], float] = {(d, h): m for d, h, m in rows}
    grid = [[by_dh.get((d.isoformat(), h), 0) or None for h in range(24)] for d in days]
    row_labels = [d.strftime("%a %m-%d") for d in days]
    col_labels = [f"{h:02d}" for h in range(24)]
    return (
        '<p class="meta">screen-time minutes per hour, last 7 days · darker = more</p>'
        + heatmap(grid, row_labels, col_labels)
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "top_apps_30d",
            "name": "Top Apps (30d)",
            "description": "Top 20 apps by total foreground time over the last 30 days.",
            "render": render_top_apps,
        },
        {
            "slug": "hourly_heatmap_7d",
            "name": "Hourly Heatmap (7d)",
            "description": "Daily-rhythm view: minutes of screen time per hour of day.",
            "render": render_hourly_heatmap,
        },
    ]
