"""Visualizations for project_time."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import calendar_grid, horizontal_bars

# Stable per-project palette so the calendar's colors mean something. Cycled
# in declaration order; non-saturated mid-tones to fit the rest of the page.
_PROJECT_PALETTE = [
    "#2e5c34", "#a04a6a", "#3a7a7a", "#cc6600", "#1a3a5e",
    "#7a4caa", "#7a5e34", "#346e7a", "#7a3434", "#5a5a5a",
]


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_total_per_project(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now().date() - timedelta(days=30)).isoformat()
    try:
        rows = con.execute(
            "SELECT project, sum(hours) AS h FROM project_time "
            "WHERE date >= ? GROUP BY project ORDER BY h DESC",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">project_time not synced yet</p>'
    finally:
        con.close()
    items = [(p, round(h, 1)) for p, h in rows if h and h > 0]
    if not items:
        return '<p class="meta">no project hours in the last 30 days</p>'
    return (
        '<p class="meta">last 30 days · total hours per project</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{v}h")
    )


def render_dominant_project_calendar(cfg: Config) -> str:
    """30-day calendar where each cell's color = the project with most hours that day."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = date.today()
    cutoff = (today - timedelta(days=30)).isoformat()
    try:
        rows = con.execute(
            "SELECT date, project, hours FROM project_time WHERE date >= ?",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">project_time not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no project_time data in the last 30 days</p>'

    by_day: dict[date, dict[str, float]] = {}
    for d_str, project, hours in rows:
        try:
            d = date.fromisoformat(d_str)
        except (TypeError, ValueError):
            continue
        by_day.setdefault(d, {})[project] = hours or 0.0

    # Stable project → color mapping by appearance order.
    project_order: list[str] = []
    seen: set[str] = set()
    for d in sorted(by_day):
        for p in by_day[d]:
            if p not in seen:
                project_order.append(p)
                seen.add(p)
    color_for = {
        p: _PROJECT_PALETTE[i % len(_PROJECT_PALETTE)] for i, p in enumerate(project_order)
    }

    dominant: dict[date, str] = {}
    intensity: dict[date, float] = {}
    for d, hours in by_day.items():
        # Only consider real categories (skip _no_data, _unaccounted)
        real = {p: h for p, h in hours.items() if not p.startswith("_") and h > 0}
        if not real:
            continue
        top = max(real.items(), key=lambda kv: kv[1])
        dominant[d] = top[0]
        intensity[d] = sum(real.values())

    def _color(_v: float) -> str:
        # Doesn't actually use _v — color comes from the dominant project lookup
        # we wire in via label_fn instead. Returning a placeholder here.
        return "#000"

    # We need calendar_grid to color by project, but its API takes a single
    # value→color fn. Workaround: expose the project as the "value" via a
    # dict keyed on date, and have color_fn map project name → palette color.
    # Easiest: synthesize a custom grid here rather than reusing calendar_grid.
    weeks = 5  # ~30 days
    grid_end = today
    grid_start = grid_end - timedelta(days=weeks * 7 - 1)
    cells_by_week: list[list[date | None]] = []
    cur = grid_start
    while cur <= grid_end:
        week = []
        for _ in range(7):
            week.append(cur if cur <= grid_end else None)
            cur += timedelta(days=1)
        cells_by_week.append(week)

    weekday_labels = ["Mon", "", "Wed", "", "Fri", "", "Sun"]
    rows_html = []
    for wd in range(7):
        cells = []
        for week in cells_by_week:
            d = week[wd]
            if d is None:
                cells.append('<td class="cal-empty"></td>')
                continue
            project = dominant.get(d)
            if not project:
                cells.append(
                    f'<td class="cal-cell" title="{d.isoformat()}: no work logged"></td>'
                )
                continue
            color = color_for.get(project, "#666")
            title = f"{d.isoformat()} · {project} · {intensity[d]:.1f}h"
            cells.append(
                f'<td class="cal-cell" style="background: {color};" title="{title}"></td>'
            )
        rows_html.append(
            f'<tr><td class="cal-rowlabel">{weekday_labels[wd]}</td>{"".join(cells)}</tr>'
        )
    grid_html = f'<table class="calendar"><tbody>{"".join(rows_html)}</tbody></table>'

    legend = " ".join(
        f'<span class="proj-legend"><span class="proj-swatch" '
        f'style="background: {color_for[p]};"></span>{p}</span>'
        for p in project_order
    )
    return (
        '<p class="meta">last ~5 weeks · cell color = dominant project that day</p>'
        + grid_html
        + f'<p class="proj-legend-line">{legend}</p>'
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "total_per_project_30d",
            "name": "Hours Per Project (30d)",
            "description": "Total hours per project over the last 30 days.",
            "render": render_total_per_project,
        },
        {
            "slug": "dominant_calendar",
            "name": "Dominant Project (5w)",
            "description": "Calendar grid colored by the project with the most hours each day.",
            "render": render_dominant_project_calendar,
        },
    ]
