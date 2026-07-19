"""Visualizations for Calendar Reality."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import horizontal_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        con = sqlite3.connect(cfg.db_path)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


def _cutoff(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def render_reality_labels(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'
    try:
        rows = con.execute(
            """
            SELECT reality_label, count(*) AS n
            FROM calendar_reality_blocks
            WHERE start_at >= ?
              AND reality_label != 'calendar_only'
            GROUP BY reality_label
            ORDER BY n DESC
            """,
            (_cutoff(14),),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">calendar tables not synced yet</p>'
    finally:
        con.close()
    items = [(row["reality_label"].replace("_", " "), float(row["n"])) for row in rows]
    return (
        '<p class="meta">past 14 days · planned calendar blocks grouped by actual activity pattern</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{int(v)}", color="var(--chart-accent)")
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "reality_labels",
            "name": "Reality Labels",
            "description": "Calendar blocks grouped by focused, fragmented, light, or no observed activity.",
            "render": render_reality_labels,
        }
    ]
