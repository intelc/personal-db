"""Visualizations for the granola tracker (meeting notes + transcripts)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from html import escape

from personal_db.config import Config
from personal_db.ui.charts import calendar_grid


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_activity_calendar(cfg: Config) -> str:
    """13-week grid: cell darkness = number of meetings started that day."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = date.today()
    weeks = 13
    start = today - timedelta(days=weeks * 7 - 1)
    try:
        rows = con.execute(
            "SELECT date(started_at, 'localtime') AS d, count(*) AS n "
            "FROM granola_documents WHERE started_at >= ? GROUP BY d",
            (start.isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">granola not synced yet</p>'
    finally:
        con.close()

    by_day: dict[date, float] = {}
    for d_str, n in rows:
        try:
            by_day[date.fromisoformat(d_str)] = float(n)
        except (TypeError, ValueError):
            continue
    if not by_day:
        return f'<p class="meta">no Granola meetings in the last {weeks} weeks</p>'
    total = int(sum(by_day.values()))
    return (
        f'<p class="meta">{total} meetings in the last {weeks} weeks · '
        "darker cells = more meetings</p>"
        + calendar_grid(by_day, end_date=today, weeks=weeks)
    )


def render_recent(cfg: Config) -> str:
    """Last 20 meetings with title, time, duration, and overview snippet."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    try:
        rows = con.execute(
            "SELECT started_at, title, overview, duration_seconds "
            "FROM granola_documents ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">granola not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no Granola meetings yet</p>'

    items = []
    for started_at, title, overview, duration in rows:
        try:
            started = datetime.fromisoformat(started_at).astimezone()
            when = started.strftime("%b %d %H:%M")
        except (TypeError, ValueError):
            when = started_at or "?"
        dur_min = round((duration or 0) / 60)
        meta = f"{when} · {dur_min}m"
        snippet = (overview or "").strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:237] + "…"
        items.append(
            f'<li><strong>{escape(title or "(untitled)")}</strong>'
            f'<span class="meta"> — {meta}</span>'
            f'{"<br>" + escape(snippet) if snippet else ""}</li>'
        )
    return f'<ul class="granola-recent">{"".join(items)}</ul>'


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "activity_calendar",
            "name": "Meeting Calendar (13w)",
            "description": "13-week grid colored by daily meeting count.",
            "render": render_activity_calendar,
        },
        {
            "slug": "recent",
            "name": "Recent Meetings",
            "description": "20 most recent Granola meetings with title, time, and overview.",
            "render": render_recent,
        },
    ]
