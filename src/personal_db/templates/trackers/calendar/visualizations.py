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


def metrics(cfg: Config) -> list[dict]:
    """Dashboard tile metrics: meetings today, the next event today, and
    meeting hours this week. Excludes all-day entries (holidays etc.) and
    soft-deleted rows (deleted_at IS NOT NULL)."""
    con = _connect(cfg)
    if not con:
        return []
    out: list[dict] = []
    try:
        today_rows = con.execute(
            """
            SELECT start_at, title FROM calendar_events
            WHERE deleted_at IS NULL AND all_day = 0
              AND date(start_at, 'localtime') = date('now', 'localtime')
            ORDER BY start_at
            """
        ).fetchall()
        week_hours = con.execute(
            """
            SELECT sum((julianday(end_at) - julianday(start_at)) * 24.0)
            FROM calendar_events
            WHERE deleted_at IS NULL AND all_day = 0
              AND start_at >= datetime('now', '-7 days')
            """
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    out.append(
        {
            "label": "Meetings today",
            "value": str(len(today_rows)),
            "detail": None,
            "delta": None,
            "good": None,
        }
    )

    now_utc = datetime.now(UTC)
    next_row = None
    for start_at, title in today_rows:
        try:
            start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if start_dt >= now_utc:
            next_row = (start_dt, title)
            break
    if next_row:
        start_dt, title = next_row
        out.append(
            {
                "label": "Next event",
                "value": start_dt.astimezone().strftime("%-I:%M %p"),
                "detail": title,
                "delta": None,
                "good": None,
            }
        )
    else:
        out.append(
            {
                "label": "Next event",
                "value": "none left today",
                "detail": None,
                "delta": None,
                "good": None,
            }
        )

    out.append(
        {
            "label": "Meeting hours (7d)",
            "value": f"{(week_hours or 0):.1f}h",
            "detail": None,
            "delta": None,
            "good": None,
        }
    )
    return out


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "reality_labels",
            "name": "Reality Labels",
            "description": "Calendar blocks grouped by focused, fragmented, light, or no observed activity.",
            "render": render_reality_labels,
        }
    ]
