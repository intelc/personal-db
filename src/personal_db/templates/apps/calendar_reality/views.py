from __future__ import annotations

import html
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from personal_db.apps import AppContext
from personal_db.db import connect
from personal_db.ui import components as c


def _cutoff(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _q(ctx: AppContext, name: str, **params: Any) -> list[dict[str, Any]]:
    try:
        return ctx.query(name, **params)
    except sqlite3.Error:
        return []


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _hours(minutes: Any) -> str:
    value = _float(minutes) / 60.0
    return f"{value:.1f}h"


def _pct(n: float, d: float) -> str:
    return "0%" if d <= 0 else f"{(n / d) * 100:.0f}%"


def _time(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        text = text.replace("T", " ")
    for suffix in ("+00:00", "Z"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.split(".", 1)[0]


def _time_range(row: dict[str, Any]) -> str:
    start = _time(row.get("start_at"))
    end = _time(row.get("end_at"))
    if len(start) >= 16 and len(end) >= 16 and start[:10] == end[:10]:
        return f"{start[:10]} {start[11:16]}-{end[11:16]}"
    return f"{start} - {end}"


def _json_labels(value: Any, *, key: str = "label", count: str = "count", limit: int = 3) -> str:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    labels = []
    for item in parsed[:limit] if isinstance(parsed, list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get(key) or "")
        metric = item.get(count)
        if not label:
            continue
        labels.append(f"{label} ({metric})" if metric is not None else label)
    return ", ".join(labels)


def _label_badge(value: Any) -> str:
    label = str(value or "unknown")
    klass = "cr-" + html.escape(label.replace("_", "-"), quote=True)
    return f'<span class="cr-label {klass}">{html.escape(label.replace("_", " "))}</span>'


def _style() -> str:
    return """
    <style>
      .cr-label {
        display: inline-block;
        padding: 2px 8px;
        border: 1px solid var(--border-strong);
        background: var(--bg-inset);
        font-size: 12px;
        line-height: 1.5;
      }
      .cr-focused { background: color-mix(in srgb, var(--ok) 18%, transparent); }
      .cr-fragmented { background: color-mix(in srgb, var(--danger) 15%, transparent); }
      .cr-light-activity { background: color-mix(in srgb, var(--warn) 20%, transparent); }
      .cr-no-activity, .cr-calendar-only { background: var(--bg-hover); }
    </style>
    """


def metrics(cfg) -> list[dict]:
    """Dashboard tile metrics: focused vs fragmented block counts over the
    past 14 days (same window and label semantics as `render_overview`'s
    `overview_counts` query -- the two most robust labels calendar_reality
    assigns; `calendar_only` blocks are excluded there too)."""
    try:
        con = connect(cfg.db_path, read_only=True)
    except sqlite3.OperationalError:
        return []
    try:
        row = con.execute(
            "SELECT count(*) AS blocks, "
            "sum(CASE WHEN reality_label = 'focused' THEN 1 ELSE 0 END) AS focused_blocks, "
            "sum(CASE WHEN reality_label = 'fragmented' THEN 1 ELSE 0 END) AS fragmented_blocks "
            "FROM calendar_reality_blocks "
            "WHERE start_at >= ? AND reality_label != 'calendar_only'",
            (_cutoff(14),),
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    if not row or not row[0]:
        return []
    _blocks, focused, fragmented = row
    return [
        {
            "label": "Focused blocks (14d)",
            "value": f"{_int(focused):,}",
            "detail": None,
            "delta": None,
            "good": None,
        },
        {
            "label": "Fragmented blocks (14d)",
            "value": f"{_int(fragmented):,}",
            "detail": None,
            "delta": None,
            "good": None,
        },
    ]


def render_overview(ctx: AppContext) -> str:
    counts = _q(ctx, "overview_counts", cutoff=_cutoff(14))
    row = counts[0] if counts else {}
    blocks = _int(row.get("blocks"))
    planned = _int(row.get("planned_minutes"))
    actual = _int(row.get("actual_minutes"))
    focused = _int(row.get("focused_blocks"))
    fragmented = _int(row.get("fragmented_blocks"))
    daily = _q(ctx, "daily_rows", cutoff=_cutoff(30))
    chart = c.chart(
        {
            "data": [
                {
                    "date": r["date"],
                    "planned": round(_float(r["planned_minutes"]) / 60.0, 2),
                    "actual": round(_float(r["actual_minutes"]) / 60.0, 2),
                    "fragmented": _int(r["fragmented"]),
                }
                for r in daily
            ],
            # AG Charts canvas options -- can't consume var(), stay fixed categorical hexes.
            "series": [
                {"type": "bar", "xKey": "date", "yKey": "planned", "yName": "Planned hours", "fill": "#94a3b8"},
                {"type": "bar", "xKey": "date", "yKey": "actual", "yName": "Observed hours", "fill": "#2563eb"},
                {"type": "line", "xKey": "date", "yKey": "fragmented", "yName": "Fragmented blocks", "stroke": "#dc2626"},
            ],
            "axes": {"bottom": {"type": "category"}, "left": {"type": "number"}},
            "legend": {"enabled": True, "position": "bottom"},
        },
        height_px=280,
    )
    labels = _q(ctx, "label_counts", cutoff=_cutoff(14))
    label_rows = [(r["reality_label"].replace("_", " "), r["n"]) for r in labels]
    return c.join_html(
        [
            _style(),
            c.metric_grid(
                [
                    ("Blocks", f"{blocks:,}", "past 14 days"),
                    ("Planned", _hours(planned), ""),
                    ("Observed", _hours(actual), _pct(actual, planned)),
                    ("Focused", f"{focused:,}", _pct(focused, blocks)),
                    ("Fragmented", f"{fragmented:,}", _pct(fragmented, blocks)),
                ]
            ),
            c.section("Planned vs Observed", chart, subtitle="Daily scheduled calendar time against observed computer activity."),
            c.section(
                "Reality Labels",
                c.data_grid(label_rows, ["Label", "Blocks"], page_size=10),
                subtitle="Heuristic labels from available activity evidence.",
            ),
        ]
    )


def render_blocks(ctx: AppContext) -> str:
    rows = _q(ctx, "recent_blocks", cutoff=_cutoff(30), limit=100)
    table = []
    for row in rows:
        table.append(
            (
                _time_range(row),
                html.escape(str(row.get("title") or "")),
                html.escape(str(row.get("calendar_title") or "")),
                f"{_int(row.get('planned_minutes'))}m",
                f"{_int(row.get('actual_minutes'))}m",
                _label_badge(row.get("reality_label")),
                f"{_float(row.get('fragmentation_score')):.2f}",
                html.escape(_json_labels(row.get("top_apps_json"), count="minutes")),
                html.escape(_json_labels(row.get("top_domains_json"))),
            )
        )
    if not table:
        body = c.empty_state(
            "No calendar blocks yet",
            hint="Calendar Reality needs the calendar tracker synced, plus Screen Time or Chrome History for observed activity.",
            action=("Go to Setup", "/setup"),
        )
    else:
        body = c.section(
            "Recent Blocks",
            c.data_grid(
                table,
                [
                    "Time",
                    "Title",
                    "Calendar",
                    "Planned",
                    "Observed",
                    "Reality",
                    "Fragmentation",
                    "Top Apps",
                    "Top Domains",
                ],
                page_size=25,
                height_px=680,
                html_columns={5},
            ),
            subtitle="Latest calendar blocks with actual app/browser evidence.",
        )
    return c.join_html([_style(), body])


def render_sources(ctx: AppContext) -> str:
    rows = _q(ctx, "calendar_rows", cutoff=_cutoff(60), limit=50)
    table = [
        (
            row.get("calendar_title") or "",
            row.get("blocks") or 0,
            _hours(row.get("planned_minutes")),
            _hours(row.get("actual_minutes")),
            row.get("focused") or 0,
            row.get("fragmented") or 0,
            f"{_float(row.get('avg_fragmentation')):.2f}",
        )
        for row in rows
    ]
    bounds = _q(ctx, "event_bounds", cutoff=_cutoff(60))
    meta = ""
    if bounds:
        first = _time(bounds[0].get("first_start"))
        last = _time(bounds[0].get("last_start"))
        imported = _int(bounds[0].get("imported_events"))
        analyzed = _int(bounds[0].get("analyzed_blocks"))
        if analyzed:
            meta = f"{imported:,} imported events · {analyzed:,} timed blocks analyzed · {first} to {last}"
        else:
            meta = f"{imported:,} imported events · no timed blocks analyzed yet"
    if not table:
        body = c.section(
            "Calendars",
            c.empty_state(
                "No calendars yet",
                hint="Calendar Reality needs the calendar tracker synced. First sync may take a few minutes.",
                action=("Go to Setup", "/setup"),
            ),
        )
    else:
        body = c.section(
            "Calendars",
            c.data_grid(
                table,
                ["Calendar", "Blocks", "Planned", "Observed", "Focused", "Fragmented", "Avg Fragmentation"],
                page_size=25,
                height_px=560,
            ),
            subtitle=meta or "Calendar-level planned-vs-actual summary.",
        )
    return c.join_html([_style(), body])
