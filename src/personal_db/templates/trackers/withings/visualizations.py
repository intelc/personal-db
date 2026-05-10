"""Visualizations for the withings tracker."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import vertical_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_weight_trend_180d(cfg: Config) -> str:
    """Daily weight (kg) over the last 180 days. Manual entries excluded.

    If there are multiple weigh-ins in a day, the latest one wins."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = datetime.now().date()
    cutoff = (today - timedelta(days=179)).isoformat()
    try:
        rows = dict(con.execute(
            "SELECT date(date) AS d, weight_kg "
            "FROM withings_measurements "
            "WHERE date >= ? AND weight_kg IS NOT NULL "
            "  AND attrib NOT IN (2, 4) "
            "GROUP BY d "
            "HAVING date = MAX(date)",
            (cutoff,),
        ).fetchall())
    except sqlite3.OperationalError:
        return '<p class="meta">withings_measurements not synced yet</p>'
    finally:
        con.close()

    items = []
    for i in range(179, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0)))

    return (
        '<p class="meta">withings weight (kg) · last 180 days · device measurements only</p>'
        + vertical_bars(items, color="#3a6ea8", show_every_nth_label=30)
    )


def render_body_composition_30d(cfg: Config) -> str:
    """Last 30 days. Bars show fat_mass_kg and lean_mass_kg side by side per day.

    The two together account for total body weight on most Withings scales,
    so the visual answers 'is recent weight change fat or lean?'."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = datetime.now().date()
    cutoff = (today - timedelta(days=29)).isoformat()
    try:
        rows = con.execute(
            "SELECT date(date) AS d, "
            "       MAX(fat_mass_kg)  AS fat, "
            "       MAX(lean_mass_kg) AS lean "
            "FROM withings_measurements "
            "WHERE date >= ? AND attrib NOT IN (2, 4) "
            "GROUP BY d",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">withings_measurements not synced yet</p>'
    finally:
        con.close()
    by_day = {row[0]: (row[1], row[2]) for row in rows}

    fat_items = []
    lean_items = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        fat, lean = by_day.get(d, (0, 0))
        fat_items.append((d[5:], fat or 0))
        lean_items.append((d[5:], lean or 0))

    return (
        '<p class="meta">withings body composition · last 30 days · '
        '<span style="color:#cc6644">fat mass kg</span> &amp; '
        '<span style="color:#3a8a4a">lean mass kg</span></p>'
        + '<div style="margin-bottom:0.5em">'
        + vertical_bars(fat_items, color="#cc6644", show_every_nth_label=5)
        + '</div>'
        + vertical_bars(lean_items, color="#3a8a4a", show_every_nth_label=5)
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "weight_trend_180d",
            "name": "Weight Trend (180d)",
            "description": "Daily weight in kilograms over the last 180 days, device measurements only.",
            "render": render_weight_trend_180d,
        },
        {
            "slug": "body_composition_30d",
            "name": "Body Composition (30d)",
            "description": "Fat mass vs lean mass, day by day, over the last 30 days.",
            "render": render_body_composition_30d,
        },
    ]
