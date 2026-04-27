"""Visualizations for codex_conversations (Codex CLI session metadata)."""

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
            "FROM codex_sessions WHERE started_at >= ? GROUP BY d",
            (cutoff,),
        ).fetchall())
    except sqlite3.OperationalError:
        return '<p class="meta">codex_sessions not synced yet</p>'
    finally:
        con.close()
    items = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0)))
    return (
        '<p class="meta">codex CLI sessions started per day · last 30 days</p>'
        + vertical_bars(items)
    )


def render_top_cwds(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        rows = con.execute(
            "SELECT cwd, count(*) AS sessions FROM codex_sessions "
            "WHERE started_at >= ? AND cwd IS NOT NULL "
            "GROUP BY cwd ORDER BY sessions DESC LIMIT 12",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">codex_sessions not synced yet</p>'
    finally:
        con.close()

    def _shorten(p: str) -> str:
        # Show last 2 path segments so a project is recognizable
        parts = [s for s in p.split("/") if s]
        if len(parts) <= 2:
            return p
        return ".../" + "/".join(parts[-2:])
    items = [(_shorten(cwd), sessions) for cwd, sessions in rows if sessions]
    return (
        '<p class="meta">last 30 days · most-used working directories</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{int(v)}")
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "sessions_per_day_30d",
            "name": "Sessions Per Day (30d)",
            "description": "Daily count of Codex CLI sessions started over the last 30 days.",
            "render": render_sessions_per_day,
        },
        {
            "slug": "top_cwds_30d",
            "name": "Top Directories (30d)",
            "description": "Working directories with the most Codex sessions.",
            "render": render_top_cwds,
        },
    ]
