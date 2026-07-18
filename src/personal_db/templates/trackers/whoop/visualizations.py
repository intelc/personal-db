"""Visualizations for whoop (cycles, recovery, sleep, workouts)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import vertical_bars
from personal_db.viz_helpers import connect_db as _connect
from personal_db.viz_helpers import meta


def render_recovery_timeline(cfg: Config) -> str:
    """Last 60 days of recovery scores. Color-coded: red <33, amber 33-66, green >66.
    The sick stretch should be obviously red."""
    con = _connect(cfg)
    if not con:
        return meta("no data")
    today = datetime.now().date()
    cutoff = (today - timedelta(days=59)).isoformat()
    try:
        rows = dict(con.execute(
            "SELECT date(start, 'localtime') AS d, recovery_score "
            "FROM whoop_recovery WHERE start >= ? AND recovery_score IS NOT NULL",
            (cutoff,),
        ).fetchall())
    except sqlite3.OperationalError:
        return meta("whoop_recovery not synced yet")
    finally:
        con.close()
    items = []
    for i in range(59, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0)))

    def _color(v):
        if v <= 0:
            return "#eee"
        if v < 33:
            return "#cc4040"
        if v < 67:
            return "#cc9933"
        return "#3a8a4a"

    return (
        '<p class="meta">whoop recovery score · last 60 days · '
        '<span style="color:#cc4040">red</span> &lt;33, '
        '<span style="color:#cc9933">amber</span> 33-66, '
        '<span style="color:#3a8a4a">green</span> &gt;66</p>'
        + vertical_bars(items, color_fn=_color, show_every_nth_label=10)
    )


def render_sleep_efficiency(cfg: Config) -> str:
    """Last 30 days of sleep_efficiency_pct. Higher = better."""
    con = _connect(cfg)
    if not con:
        return meta("no data")
    today = datetime.now().date()
    cutoff = (today - timedelta(days=29)).isoformat()
    try:
        rows = dict(con.execute(
            "SELECT date(start, 'localtime') AS d, sleep_efficiency_pct "
            "FROM whoop_sleep WHERE start >= ? AND sleep_efficiency_pct IS NOT NULL "
            "  AND nap = 0",  # exclude naps so the daily series is clean
            (cutoff,),
        ).fetchall())
    except sqlite3.OperationalError:
        return meta("whoop_sleep not synced yet")
    finally:
        con.close()
    items = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0)))
    return (
        '<p class="meta">whoop sleep efficiency · last 30 days · higher is better (naps excluded)</p>'
        + vertical_bars(items, color="var(--chart-accent)", show_every_nth_label=5)
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "recovery_timeline_60d",
            "name": "Recovery Timeline (60d)",
            "description": "Daily recovery score over the last 60 days, color-graded by zone.",
            "render": render_recovery_timeline,
        },
        {
            "slug": "sleep_efficiency_30d",
            "name": "Sleep Efficiency (30d)",
            "description": "Daily sleep efficiency percentage over the last 30 days.",
            "render": render_sleep_efficiency,
        },
    ]
