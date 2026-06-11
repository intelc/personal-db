from __future__ import annotations

import html
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from personal_db.apps import AppContext
from personal_db.ui import agcharts
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


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return "0%"
    return f"{(n / d) * 100:.0f}%"


def _time(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        text = text.replace("T", " ")
    for suffix in ("+00:00", "Z"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.split(".", 1)[0]


def _impact_counts(ctx: AppContext, days: int) -> dict[str, int]:
    return {str(row["impact"]): _int(row["n"]) for row in _q(ctx, "impact_counts", cutoff=_cutoff(days))}


def _impact_badge(value: Any) -> str:
    impact = str(value or "unknown")
    label = impact.replace("_", " ")
    klass = "impact-" + html.escape(impact.replace("_", "-"), quote=True)
    return f'<span class="attention-impact {klass}">{html.escape(label)}</span>'


def _style() -> str:
    return """
    <style>
      .attention-impact {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        background: #eef2ff;
        color: #1e3a8a;
        font-size: 12px;
        line-height: 1.6;
      }
      .attention-impact.impact-ignored { background: #f3f4f6; color: #374151; }
      .attention-impact.impact-glanced { background: #ecfeff; color: #155e75; }
      .attention-impact.impact-batched { background: #fef9c3; color: #854d0e; }
      .attention-impact.impact-acted-on { background: #dcfce7; color: #166534; }
      .attention-impact.impact-derailed { background: #fee2e2; color: #991b1b; }
    </style>
    """


def render_overview(ctx: AppContext) -> str:
    counts = _impact_counts(ctx, 7)
    total = sum(counts.values())
    acted = counts.get("acted_on", 0) + counts.get("derailed", 0)
    derailed = counts.get("derailed", 0)
    ignored = counts.get("ignored", 0)
    batched = counts.get("batched", 0)

    daily = _q(ctx, "daily_rows", cutoff=_cutoff(30))
    daily_chart = c.chart(
        {
            "data": [
                {
                    "day": row["day"],
                    "notifications": _int(row["notifications"]),
                    "acted": _int(row["acted"]),
                    "derailed": _int(row["derailed"]),
                }
                for row in daily
            ],
            "series": [
                {"type": "bar", "xKey": "day", "yKey": "notifications", "yName": "Sent", "fill": "#94a3b8"},
                {"type": "bar", "xKey": "day", "yKey": "acted", "yName": "Acted", "fill": "#2563eb"},
                {"type": "bar", "xKey": "day", "yKey": "derailed", "yName": "Derailed", "fill": "#dc2626"},
            ],
            "axes": {"bottom": {"type": "category"}, "left": {"type": "number"}},
            "legend": {"enabled": True, "position": "bottom"},
        },
        height_px=260,
    )
    hourly = _q(ctx, "hourly_rows", cutoff=_cutoff(14))
    hourly_chart = agcharts.chart(
        {
            "data": [
                {
                    "hour": f"{_int(row['hour']):02d}",
                    "acted": _int(row["acted"]),
                    "derailed": _int(row["derailed"]),
                }
                for row in hourly
            ],
            "series": [
                {"type": "bar", "xKey": "hour", "yKey": "acted", "yName": "Acted", "fill": "#2563eb"},
                {"type": "bar", "xKey": "hour", "yKey": "derailed", "yName": "Derailed", "fill": "#dc2626"},
            ],
            "axes": {"bottom": {"type": "category"}, "left": {"type": "number"}},
            "legend": {"enabled": True, "position": "bottom"},
        },
        height_px=220,
    )
    return c.join_html(
        [
        _style(),
        c.metric_grid(
            [
                ("Notifications", f"{total:,}", "past 7 days"),
                ("Acted on", f"{acted:,}", _pct(acted, total)),
                ("Derailed", f"{derailed:,}", _pct(derailed, total)),
                ("Ignored", f"{ignored:,}", _pct(ignored, total)),
                ("Batched", f"{batched:,}", "burst noise"),
            ]
        ),
        c.section("Daily Impact", daily_chart, subtitle="Sent, acted-on, and derailing notifications over 30 days."),
        c.section("Interruption Hours", hourly_chart, subtitle="Hours where notifications most often get acted on."),
        ]
    )


def render_sources(ctx: AppContext) -> str:
    rows = _q(ctx, "source_rows", cutoff=_cutoff(30), limit=50)
    table_rows = [
        (
            row.get("source") or "",
            row.get("notifications") or 0,
            row.get("ignored") or 0,
            row.get("glanced") or 0,
            row.get("batched") or 0,
            row.get("acted_on") or 0,
            row.get("derailed") or 0,
            f"{float(row.get('action_rate') or 0):.1f}%",
            f"{float(row.get('avg_seconds_to_action') or 0):.0f}s",
        )
        for row in rows
    ]
    columns = [
        "Source",
        "Notifications",
        "Ignored",
        "Glanced",
        "Batched",
        "Acted On",
        "Derailed",
        "Action Rate",
        "Avg Seconds",
    ]
    return c.join_html(
        [
        _style(),
        c.section(
            "Source Ranking",
            c.data_grid(table_rows, columns, page_size=25, height_px=620),
            subtitle="Past 30 days. Highest derailment and action rates are the sources worth tuning first.",
        ),
        ]
    )


def render_events(ctx: AppContext) -> str:
    rows = _q(ctx, "recent_events", limit=100)
    table_rows = []
    for row in rows:
        table_rows.append(
            (
                _time(row["delivered_at"]),
                html.escape(str(row["source"] or "")),
                _impact_badge(row["impact"]),
                f"{float(row['confidence'] or 0):.2f}",
                str(row["batch_count"] or ""),
                str(row["seconds_to_action"] or ""),
                html.escape(str(row["prior_app_name"] or "")),
                html.escape(str(row["next_app_name"] or "")),
                html.escape(str(row["evidence"] or "")),
            )
        )
    return c.join_html(
        [
        _style(),
        c.section(
            "Recent Inferences",
            c.data_grid(
                table_rows,
                [
                    "delivered",
                    "source",
                    "impact",
                    "confidence",
                    "batch",
                    "seconds",
                    "prior app",
                    "next app",
                    "evidence",
                ],
                page_size=25,
                height_px=680,
                html_columns={2},
            ),
            subtitle="Latest notifications with nearby context evidence.",
        ),
        ]
    )
