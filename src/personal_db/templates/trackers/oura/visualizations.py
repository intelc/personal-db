"""Visualizations for Oura Ring data."""

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


def _zone_color(value: int | float) -> str:
    if not value or value <= 0:
        return "#eee"
    if value < 60:
        return "#cc4040"
    if value < 80:
        return "#cc9933"
    return "#3a8a4a"


def _daily_series(
    cfg: Config, table: str, column: str, days: int
) -> list[tuple[str, float | int]] | None:
    con = _connect(cfg)
    if not con:
        return None
    today = datetime.now().date()
    cutoff = (today - timedelta(days=days - 1)).isoformat()
    try:
        rows = dict(
            con.execute(
                f"SELECT day, {column} FROM {table} "
                f"WHERE day >= ? AND {column} IS NOT NULL",
                (cutoff,),
            ).fetchall()
        )
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    items: list[tuple[str, float | int]] = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0)))
    return items


def render_readiness_60d(cfg: Config) -> str:
    items = _daily_series(cfg, "oura_daily_readiness", "score", 60)
    if items is None:
        return '<p class="meta">oura_daily_readiness not synced yet</p>'
    return (
        '<p class="meta">oura readiness · last 60 days · '
        '<span style="color:#cc4040">red</span> &lt;60, '
        '<span style="color:#cc9933">amber</span> 60-79, '
        '<span style="color:#3a8a4a">green</span> &ge;80</p>'
        + vertical_bars(items, color_fn=_zone_color, show_every_nth_label=10)
    )


def render_sleep_score_30d(cfg: Config) -> str:
    items = _daily_series(cfg, "oura_daily_sleep", "score", 30)
    if items is None:
        return '<p class="meta">oura_daily_sleep not synced yet</p>'
    return (
        '<p class="meta">oura sleep score · last 30 days</p>'
        + vertical_bars(items, color_fn=_zone_color, show_every_nth_label=5)
    )


def render_steps_30d(cfg: Config) -> str:
    items = _daily_series(cfg, "oura_daily_activity", "steps", 30)
    if items is None:
        return '<p class="meta">oura_daily_activity not synced yet</p>'
    return (
        '<p class="meta">oura steps · last 30 days</p>'
        + vertical_bars(items, color="#1a3a5e", show_every_nth_label=5)
    )


def render_hrv_30d(cfg: Config) -> str:
    """Average sleep HRV per night, last 30 days. Higher = better recovery."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = datetime.now().date()
    cutoff = (today - timedelta(days=29)).isoformat()
    try:
        rows = dict(
            con.execute(
                "SELECT day, MAX(average_hrv) FROM oura_sleep "
                "WHERE day >= ? AND average_hrv IS NOT NULL "
                "  AND type IN ('long_sleep','sleep') "
                "GROUP BY day",
                (cutoff,),
            ).fetchall()
        )
    except sqlite3.OperationalError:
        return '<p class="meta">oura_sleep not synced yet</p>'
    finally:
        con.close()
    items: list[tuple[str, float]] = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0) or 0))
    return (
        '<p class="meta">oura sleep HRV (ms RMSSD) · last 30 days · higher is better</p>'
        + vertical_bars(items, color="#3a8a4a", show_every_nth_label=5)
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "readiness_60d",
            "name": "Readiness (60d)",
            "description": "Daily Oura readiness score over the last 60 days.",
            "render": render_readiness_60d,
        },
        {
            "slug": "sleep_score_30d",
            "name": "Sleep Score (30d)",
            "description": "Daily Oura sleep score over the last 30 days.",
            "render": render_sleep_score_30d,
        },
        {
            "slug": "hrv_30d",
            "name": "Sleep HRV (30d)",
            "description": "Average sleep HRV per night over the last 30 days.",
            "render": render_hrv_30d,
        },
        {
            "slug": "steps_30d",
            "name": "Steps (30d)",
            "description": "Daily step count over the last 30 days.",
            "render": render_steps_30d,
        },
    ]
