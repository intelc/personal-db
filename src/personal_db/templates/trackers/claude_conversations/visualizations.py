"""Visualizations for claude_conversations (Claude Code session metadata)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import horizontal_bars, vertical_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_sessions_per_day(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = datetime.now().date()
    cutoff = (today - timedelta(days=29)).isoformat()
    try:
        rows = dict(con.execute(
            "SELECT date(started_at, 'localtime') AS d, count(*) "
            "FROM claude_sessions WHERE started_at >= ? GROUP BY d",
            (cutoff,),
        ).fetchall())
    except sqlite3.OperationalError:
        return '<p class="meta">claude_sessions not synced yet</p>'
    finally:
        con.close()
    items = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0)))
    return (
        '<p class="meta">claude code sessions started per day · last 30 days</p>'
        + vertical_bars(items)
    )


def render_top_projects(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        rows = con.execute(
            "SELECT project_slug, count(*) AS sessions, "
            "       sum(coalesce(user_msg_count, 0)) AS msgs "
            "FROM claude_sessions WHERE started_at >= ? "
            "GROUP BY project_slug ORDER BY sessions DESC LIMIT 12",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">claude_sessions not synced yet</p>'
    finally:
        con.close()
    # Display short project names (slug → last segment after final '-')
    def _shorten(slug: str) -> str:
        if not slug:
            return "(none)"
        return slug.rstrip("-").split("-")[-1] or slug
    items = [(_shorten(slug), sessions) for slug, sessions, _ in rows if sessions]
    return (
        '<p class="meta">last 30 days · top projects by session count</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{int(v)}")
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "sessions_per_day_30d",
            "name": "Sessions Per Day (30d)",
            "description": "Daily count of Claude Code sessions started over the last 30 days.",
            "render": render_sessions_per_day,
        },
        {
            "slug": "top_projects_30d",
            "name": "Top Projects (30d)",
            "description": "Projects with the most Claude Code sessions in the last 30 days.",
            "render": render_top_projects,
        },
    ]
