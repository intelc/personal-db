"""Visualizations for Oura Ring data."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import vertical_bars
from personal_db.viz_helpers import connect_db as _connect
from personal_db.viz_helpers import daily_series as _daily_series
from personal_db.viz_helpers import meta


def _zone_color(value: int | float) -> str:
    if not value or value <= 0:
        return "var(--chart-grid)"
    if value < 60:
        return "var(--chart-red)"
    if value < 80:
        return "var(--chart-amber)"
    return "var(--chart-green)"


def render_readiness_60d(cfg: Config) -> str:
    items = _daily_series(cfg, "oura_daily_readiness", "score", 60)
    if items is None:
        return meta("oura_daily_readiness not synced yet")
    return (
        '<p class="meta">oura readiness · last 60 days · '
        '<span style="color:var(--chart-red)">red</span> &lt;60, '
        '<span style="color:var(--chart-amber)">amber</span> 60-79, '
        '<span style="color:var(--chart-green)">green</span> &ge;80</p>'
        + vertical_bars(items, color_fn=_zone_color, show_every_nth_label=10)
    )


def render_sleep_score_30d(cfg: Config) -> str:
    items = _daily_series(cfg, "oura_daily_sleep", "score", 30)
    if items is None:
        return meta("oura_daily_sleep not synced yet")
    return (
        '<p class="meta">oura sleep score · last 30 days</p>'
        + vertical_bars(items, color_fn=_zone_color, show_every_nth_label=5)
    )


def render_steps_30d(cfg: Config) -> str:
    items = _daily_series(cfg, "oura_daily_activity", "steps", 30)
    if items is None:
        return meta("oura_daily_activity not synced yet")
    return (
        '<p class="meta">oura steps · last 30 days</p>'
        + vertical_bars(items, color="var(--chart-accent)", show_every_nth_label=5)
    )


def render_hrv_30d(cfg: Config) -> str:
    """Average sleep HRV per night, last 30 days. Higher = better recovery."""
    con = _connect(cfg)
    if not con:
        return meta("no data")
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
        return meta("oura_sleep not synced yet")
    finally:
        con.close()
    items: list[tuple[str, float]] = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0) or 0))
    return (
        '<p class="meta">oura sleep HRV (ms RMSSD) · last 30 days · higher is better</p>'
        + vertical_bars(items, color="var(--chart-green)", show_every_nth_label=5)
    )


def _as_of(day_str: str) -> str | None:
    """Human 'as of <date>' suffix when the reading isn't from today."""
    try:
        d = date.fromisoformat(day_str)
    except (ValueError, TypeError):
        return None
    if d == datetime.now().date():
        return None
    return f"as of {d.isoformat()}"


def _zone_good(value: int | float | None) -> bool | None:
    if value is None:
        return None
    if value >= 80:
        return True
    if value < 60:
        return False
    return None


def metrics(cfg: Config) -> list[dict]:
    """Dashboard tile metrics: latest readiness and sleep scores. Oura syncs
    can lag well behind 'today', so each value carries an explicit
    'as of <date>' detail rather than hiding staleness."""
    con = _connect(cfg)
    if not con:
        return []
    out: list[dict] = []
    try:
        readiness = con.execute(
            "SELECT day, score FROM oura_daily_readiness "
            "WHERE score IS NOT NULL ORDER BY day DESC LIMIT 1"
        ).fetchone()
        sleep = con.execute(
            "SELECT day, score FROM oura_daily_sleep "
            "WHERE score IS NOT NULL ORDER BY day DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    if readiness:
        day, score = readiness
        out.append(
            {
                "label": "Readiness",
                "value": str(int(score)),
                "detail": _as_of(day),
                "delta": None,
                "good": _zone_good(score),
            }
        )
    if sleep:
        day, score = sleep
        out.append(
            {
                "label": "Sleep score",
                "value": str(int(score)),
                "detail": _as_of(day),
                "delta": None,
                "good": _zone_good(score),
            }
        )
    return out


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
