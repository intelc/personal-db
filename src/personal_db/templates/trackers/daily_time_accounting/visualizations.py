"""Visualizations for the daily_time_accounting tracker.

Returns HTML fragments wrapped in a single root element. Pixel-aesthetic
classes (.stack, .seg, .legend, etc.) come from the shared style.css.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from html import escape

from personal_db.config import Config

# Color palette per category — desaturated pixel-art tones, used only inside
# the data viz (the shell stays B&W).
_CAT_COLORS = {
    "sleep": "#1a3a5e",
    "workout": "#cc6600",
    "work": "#2e5c34",
    "communication": "#3a7a7a",
    "leisure": "#a04a6a",
    "other_screen": "#666666",
    "_unaccounted": "#cccccc",
    "_no_data": "url(#hatch)",
}


def _local_today() -> date:
    return datetime.now().astimezone().date()


def _query_breakdown(cfg: Config, day: date) -> list[dict]:
    try:
        con = sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return []
    try:
        rows = con.execute(
            "SELECT category, hours FROM daily_time_accounting "
            "WHERE date = ? ORDER BY hours DESC",
            (day.isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [
        {"category": c, "hours": round(h, 2), "color": _CAT_COLORS.get(c, "#888")}
        for c, h in rows
        if h > 0.01
    ]


def render_today_stack(cfg: Config) -> str:
    day = _local_today()
    breakdown = _query_breakdown(cfg, day)
    if not breakdown:
        return '<p class="meta">no data yet for today — run sync</p>'

    segs = []
    for s in breakdown:
        title = f"{s['category']} · {s['hours']}h"
        label = ""
        if s["hours"] >= 1.0:
            label = (
                f'<span class="seg-label">{escape(s["category"])}</span>'
                f'<span class="seg-val">{s["hours"]}h</span>'
            )
        segs.append(
            f'<div class="seg" style="flex: {s["hours"]}; background: {s["color"]};" '
            f'title="{escape(title)}">{label}</div>'
        )
    legend = "".join(
        f'<tr><td class="swatch"><span style="background: {s["color"]};"></span></td>'
        f'<td class="cat">{escape(s["category"])}</td>'
        f'<td class="hrs">{s["hours"]}h</td></tr>'
        for s in breakdown
    )
    total = round(sum(s["hours"] for s in breakdown), 1)
    return (
        f'<div class="stack">{"".join(segs)}</div>'
        f'<table class="legend"><tbody>{legend}</tbody></table>'
        f'<p class="meta">{total}h accounted of 24h · {day.strftime("%A")} {day.isoformat()}</p>'
    )


def render_recent_7d(cfg: Config) -> str:
    today = _local_today()
    rows_html = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        breakdown = _query_breakdown(cfg, d)
        if breakdown:
            segs = "".join(
                f'<div class="seg" style="flex: {s["hours"]}; background: {s["color"]};" '
                f'title="{escape(s["category"])} · {s["hours"]}h"></div>'
                for s in breakdown
            )
        else:
            segs = '<div class="seg empty">no data</div>'
        rows_html.append(
            f'<tr><td class="day-label">{d.strftime("%a")}<br>'
            f'<span class="day-date">{d.strftime("%m-%d")}</span></td>'
            f'<td class="day-bar"><div class="stack thin">{segs}</div></td></tr>'
        )
    return f'<table class="recent"><tbody>{"".join(rows_html)}</tbody></table>'


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "today_stack",
            "name": "Today's Time",
            "description": "Horizontal stack of today's hours by category, with legend.",
            "render": render_today_stack,
        },
        {
            "slug": "recent_7d",
            "name": "Last 7 Days",
            "description": "Day-by-day stack bars for the past week.",
            "render": render_recent_7d,
        },
    ]
