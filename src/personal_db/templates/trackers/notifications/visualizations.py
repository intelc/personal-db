"""Visualizations for macOS notification attention impact."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

from personal_db.config import Config
from personal_db.ui.charts import heatmap, horizontal_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        con = sqlite3.connect(cfg.db_path)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


def _cutoff(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _q(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(con.execute(sql, params))


def render_impact_summary(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'
    try:
        rows = _q(
            con,
            """
            SELECT impact, count(*) AS n
            FROM notification_impacts
            WHERE delivered_at >= ?
            GROUP BY impact
            ORDER BY n DESC
            """,
            (_cutoff(7),),
        )
    except sqlite3.OperationalError:
        return '<p class="meta">notifications tables not synced yet</p>'
    finally:
        con.close()
    items = [(row["impact"].replace("_", " "), float(row["n"])) for row in rows]
    return (
        '<p class="meta">past 7 days · inferred from nearby Mosspath Lite activity when available</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{int(v)}", color="var(--chart-accent)")
    )


def render_source_impact(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'
    try:
        rows = _q(
            con,
            """
            SELECT coalesce(app_name, bundle_id, '(unknown)') AS source,
                   count(*) AS notifications,
                   sum(CASE WHEN impact IN ('acted_on', 'derailed') THEN 1 ELSE 0 END) AS active,
                   sum(CASE WHEN impact = 'derailed' THEN 1 ELSE 0 END) AS derailed
            FROM notification_impacts
            WHERE delivered_at >= ?
            GROUP BY source
            HAVING notifications > 0
            ORDER BY derailed DESC, active DESC, notifications DESC
            LIMIT 12
            """,
            (_cutoff(14),),
        )
    except sqlite3.OperationalError:
        return '<p class="meta">notifications tables not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no notification impact rows yet</p>'
    table = []
    for row in rows:
        rate = (float(row["active"] or 0) / float(row["notifications"] or 1)) * 100
        table.append(
            "<tr>"
            f"<td>{escape(row['source'])}</td>"
            f"<td>{int(row['notifications'])}</td>"
            f"<td>{int(row['active'] or 0)}</td>"
            f"<td>{int(row['derailed'] or 0)}</td>"
            f"<td>{rate:.0f}%</td>"
            "</tr>"
        )
    return (
        '<p class="meta">past 14 days · ranked by derailments and acted-on notifications</p>'
        '<div class="recent-rows-wrap"><table class="recent-rows">'
        "<thead><tr><th>source</th><th>sent</th><th>acted</th><th>derailed</th><th>action rate</th></tr></thead>"
        f"<tbody>{''.join(table)}</tbody></table></div>"
    )


def render_hourly_heatmap(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'
    try:
        rows = _q(
            con,
            """
            SELECT date(delivered_at, 'localtime') AS d,
                   cast(strftime('%H', delivered_at, 'localtime') AS INTEGER) AS h,
                   sum(CASE WHEN impact IN ('acted_on', 'derailed') THEN 1 ELSE 0 END) AS n
            FROM notification_impacts
            WHERE delivered_at >= ?
            GROUP BY d, h
            """,
            (_cutoff(7),),
        )
    except sqlite3.OperationalError:
        return '<p class="meta">notifications tables not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no acted-on notifications in the last 7 days</p>'

    today = datetime.now().astimezone().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    by_day_hour = {(row["d"], row["h"]): row["n"] for row in rows}
    grid = [[by_day_hour.get((day.isoformat(), hour)) for hour in range(24)] for day in days]
    row_labels = [day.strftime("%a %m-%d") for day in days]
    col_labels = [f"{hour:02d}" for hour in range(24)]
    return (
        '<p class="meta">acted-on or derailing notifications per hour, last 7 days</p>'
        + heatmap(grid, row_labels, col_labels, base_color=(37, 99, 235))
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "impact_summary",
            "name": "Impact Summary",
            "description": "Ignored, glanced, acted-on, batched, and derailing notification counts.",
            "render": render_impact_summary,
        },
        {
            "slug": "source_impact",
            "name": "Source Impact",
            "description": "Notification sources ranked by action and derailment.",
            "render": render_source_impact,
        },
        {
            "slug": "hourly_heatmap",
            "name": "Impact Heatmap",
            "description": "When notifications most often become acted-on interruptions.",
            "render": render_hourly_heatmap,
        },
    ]
