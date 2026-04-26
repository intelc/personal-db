"""Visualizations for project_time."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import horizontal_bars, stacked_vertical_bars

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


def render_daily_stack(cfg: Config) -> str:
    """Stacked daily bars showing project-time mix over the last 30 days.

    Each day = one vertical bar, segments stacked bottom-up by project. Bar
    heights normalized to the busiest day in the window so a quiet day is
    visibly shorter. Same project palette is reused across days so colors
    are stable.
    """
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = date.today()
    cutoff = (today - timedelta(days=29)).isoformat()
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

    # Group by date → {project: hours}, dropping the underscore-prefixed
    # synthetic categories (_no_data, _unaccounted, etc.) that don't belong
    # in a "real work" view.
    by_day: dict[date, dict[str, float]] = {}
    for d_str, project, hours in rows:
        if project.startswith("_") or not hours or hours <= 0:
            continue
        try:
            d = date.fromisoformat(d_str)
        except (TypeError, ValueError):
            continue
        by_day.setdefault(d, {})[project] = hours

    # Stable project → color mapping by total-hours rank (biggest projects
    # get the front of the palette so their color is most distinctive).
    totals: dict[str, float] = {}
    for hours in by_day.values():
        for p, h in hours.items():
            totals[p] = totals.get(p, 0) + h
    project_order = sorted(totals.keys(), key=lambda p: (-totals[p], p))
    color_for = {
        p: _PROJECT_PALETTE[i % len(_PROJECT_PALETTE)] for i, p in enumerate(project_order)
    }

    # Build one bar per day across the full 30-day window so empty days show
    # as zero-height bars (tells the eye "nothing happened" vs "no data").
    bars: list[dict] = []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        day_hours = by_day.get(d, {})
        # Segments in project_order — keeps colors in the same vertical
        # position across days so the eye can track a project over time.
        segments = [(p, color_for[p], day_hours.get(p, 0.0)) for p in project_order]
        bars.append({"label": d.strftime("%m-%d"), "segments": segments})

    legend = " ".join(
        f'<span class="proj-legend"><span class="proj-swatch" '
        f'style="background: {color_for[p]};"></span>{p}'
        f' <span class="meta">({totals[p]:.1f}h)</span></span>'
        for p in project_order
    )
    return (
        '<p class="meta">last 30 days · daily project-time mix · '
        "bar height scaled to the busiest day</p>"
        + stacked_vertical_bars(bars, value_unit="h")
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
            "slug": "daily_stack_30d",
            "name": "Daily Stack (30d)",
            "description": "Stacked daily bars showing project-time mix over the last 30 days.",
            "render": render_daily_stack,
        },
    ]
