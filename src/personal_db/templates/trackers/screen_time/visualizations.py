"""Visualizations for screen_time_app_usage.

The screen_time tracker itself only ingests Mac usage (knowledgeC.db /app/usage).
For computer vs phone split, we read cross-database from Mosspath's events.sqlite,
which captures sessions tagged with platform (mac, iphone, watch, unknown).

App-name resolution lives in our own ``screen_time_app_names`` table, populated
during sync. Renders only read from it — never call resolve_app_name() at render
time and never read mosspath's app_name column.
"""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from personal_db.config import Config
from personal_db.ui.charts import heatmap, horizontal_bars, stacked_vertical_bars

_MOSSPATH_DB = Path("~/Library/Application Support/Mosspath/store/events.sqlite").expanduser()


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def _load_name_cache(cfg: Config) -> dict[str, str]:
    """Bundle-id → friendly name, sourced from screen_time_app_names."""
    con = _connect(cfg)
    if not con:
        return {}
    try:
        return {
            row[0]: row[1]
            for row in con.execute(
                "SELECT bundle_id, app_name FROM screen_time_app_names"
            )
        }
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()


def _render_top_apps_for_platform(cfg: Config, platform: str, label: str) -> str:
    """Top apps over last 30 days for a given Mosspath platform, in hours/day average."""
    if not _MOSSPATH_DB.exists():
        return (
            '<p class="meta">Mosspath events.sqlite not found at '
            f'<code>{html.escape(str(_MOSSPATH_DB))}</code> — phone/per-platform data unavailable.</p>'
        )
    cutoff_ts = int((datetime.now() - timedelta(days=30)).timestamp())
    try:
        con = sqlite3.connect(f"file:{_MOSSPATH_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT bundle_id, sum(duration_seconds)/3600.0 AS hours, count(*) AS sessions "
            "FROM screen_time_sessions "
            "WHERE platform = ? AND start_timestamp >= ? "
            "GROUP BY bundle_id ORDER BY hours DESC LIMIT 20",
            (platform, cutoff_ts),
        ).fetchall()
        days_with_data = con.execute(
            "SELECT count(DISTINCT date(start_timestamp, 'unixepoch', 'localtime')) "
            "FROM screen_time_sessions WHERE platform = ? AND start_timestamp >= ?",
            (platform, cutoff_ts),
        ).fetchone()[0]
        con.close()
    except sqlite3.OperationalError as e:
        return f'<p class="meta">cannot read Mosspath events.sqlite: {html.escape(str(e))}</p>'

    if not rows or not days_with_data:
        return f'<p class="meta">no {label} data in last 30 days</p>'

    name_by_bundle = _load_name_cache(cfg)
    items: list[tuple[str, float]] = []
    for bundle_id, hours, sessions in rows:
        if not hours:
            continue
        name = name_by_bundle.get(bundle_id, bundle_id)
        per_day = hours / days_with_data
        items.append((f"{name} ({sessions} sess)", round(per_day, 2)))

    return (
        f'<p class="meta">top 20 {label} apps · last 30 days · '
        f'hours per day, averaged over {days_with_data} active days · '
        'session count in parens</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{v} h/day")
    )


def render_top_apps_mac_30d(cfg: Config) -> str:
    return _render_top_apps_for_platform(cfg, "mac", "Mac")


def render_top_apps_phone_30d(cfg: Config) -> str:
    return _render_top_apps_for_platform(cfg, "iphone", "iPhone")


def render_hourly_heatmap(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    try:
        rows = con.execute(
            "SELECT date(start_at, 'localtime') AS d, "
            "       cast(strftime('%H', start_at, 'localtime') AS INTEGER) AS h, "
            "       sum(seconds)/60.0 AS minutes "
            "FROM screen_time_app_usage WHERE start_at >= ? GROUP BY d, h",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">screen_time_app_usage not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no usage in the last 7 days</p>'
    today = datetime.now().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    by_dh: dict[tuple[str, int], float] = {(d, h): m for d, h, m in rows}
    grid = [[by_dh.get((d.isoformat(), h), 0) or None for h in range(24)] for d in days]
    row_labels = [d.strftime("%a %m-%d") for d in days]
    col_labels = [f"{h:02d}" for h in range(24)]
    return (
        '<p class="meta">screen-time minutes per hour, last 7 days · darker = more</p>'
        + heatmap(grid, row_labels, col_labels)
    )


def render_device_split_30d(cfg: Config) -> str:
    """Mac vs iPhone hours per day, last 30 days, sourced from Mosspath."""
    if not _MOSSPATH_DB.exists():
        return (
            '<p class="meta">Mosspath events.sqlite not found at '
            f'<code>{html.escape(str(_MOSSPATH_DB))}</code> — phone data unavailable. '
            'Install Mosspath and let it run to capture iPhone screen time.</p>'
        )

    cutoff_ts = int((datetime.now() - timedelta(days=30)).timestamp())
    try:
        con = sqlite3.connect(f"file:{_MOSSPATH_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT platform, "
            "       date(start_timestamp, 'unixepoch', 'localtime') AS d, "
            "       sum(duration_seconds)/3600.0 AS hours "
            "FROM screen_time_sessions "
            "WHERE platform IN ('mac', 'iphone') AND start_timestamp >= ? "
            "GROUP BY platform, d",
            (cutoff_ts,),
        ).fetchall()
        con.close()
    except sqlite3.OperationalError as e:
        return f'<p class="meta">cannot read Mosspath events.sqlite: {html.escape(str(e))}</p>'

    if not rows:
        return '<p class="meta">no Mac or iPhone screen-time sessions in the last 30 days</p>'

    by: dict[tuple[str, str], float] = {(d, p): h for p, d, h in rows}
    today = datetime.now().date()
    bars = []
    total_mac = 0.0
    total_phone = 0.0
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        mac_h = by.get((d, "mac"), 0)
        ip_h = by.get((d, "iphone"), 0)
        total_mac += mac_h
        total_phone += ip_h
        # Segment names include date so hover tooltip is self-describing.
        bars.append(
            {
                "label": d[5:],
                "segments": [
                    (f"mac {d[5:]}", "var(--chart-accent)", round(mac_h, 1)),
                    (f"iphone {d[5:]}", "#d97706", round(ip_h, 1)),
                ],
            }
        )

    return (
        f'<p class="meta">last 30 days · '
        f'<span style="color:var(--chart-accent)">●</span> mac {round(total_mac, 1)}h · '
        f'<span style="color:#d97706">●</span> iphone {round(total_phone, 1)}h · '
        f'data from Mosspath events.sqlite</p>'
        + stacked_vertical_bars(bars, value_unit="h")
    )


def _merge_sessions(rows: list[tuple], gap_s: float = 30.0) -> list[dict]:
    """Coalesce consecutive sessions of same (platform, bundle_id) with small gap.

    Reduces ~1200 raw 24h sessions to ~300 distinct blocks for the flame graph,
    so the hover surface is actually clickable. Drops ordering across platforms;
    each platform's stream is merged independently so cross-platform timing is
    preserved.
    """
    by_platform: dict[str, list] = {}
    for r in rows:
        by_platform.setdefault(r[0], []).append(r)
    merged: list[dict] = []
    for platform, plist in by_platform.items():
        plist.sort(key=lambda r: r[2])  # by start_timestamp
        for platform_, bundle, start_ts, end_ts, dur in plist:
            if (
                merged
                and merged[-1]["platform"] == platform_
                and merged[-1]["bundle"] == bundle
                and start_ts - merged[-1]["end"] < gap_s
            ):
                merged[-1]["end"] = end_ts
                merged[-1]["dur"] += dur
            else:
                merged.append(
                    {
                        "platform": platform_,
                        "bundle": bundle,
                        "start": start_ts,
                        "end": end_ts,
                        "dur": dur,
                    }
                )
    return merged


def render_device_flame_24h(cfg: Config) -> str:
    """Flame-graph timeline of mac + iphone sessions across the last 24h.

    Two horizontal lanes (mac top, iphone bottom) on a shared x-axis so
    cross-device overlap is visible. Each rectangle is one (merged) session;
    hover via native SVG <title> reveals app name, duration, and time range.
    """
    if not _MOSSPATH_DB.exists():
        return (
            '<p class="meta">Mosspath events.sqlite not found at '
            f'<code>{html.escape(str(_MOSSPATH_DB))}</code> — phone data unavailable.</p>'
        )

    now = datetime.now()
    cutoff = now - timedelta(hours=24)
    cutoff_ts = cutoff.timestamp()
    now_ts = now.timestamp()

    try:
        con = sqlite3.connect(f"file:{_MOSSPATH_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT platform, bundle_id, start_timestamp, end_timestamp, "
            "       duration_seconds "
            "FROM screen_time_sessions "
            "WHERE platform IN ('mac', 'iphone') "
            "  AND end_timestamp > ? AND start_timestamp < ? "
            "ORDER BY start_timestamp",
            (cutoff_ts, now_ts),
        ).fetchall()
        con.close()
    except sqlite3.OperationalError as e:
        return f'<p class="meta">cannot read Mosspath events.sqlite: {html.escape(str(e))}</p>'

    if not rows:
        return '<p class="meta">no Mac or iPhone screen-time sessions in the last 24 hours</p>'

    sessions = _merge_sessions(rows)
    name_by_bundle = _load_name_cache(cfg)

    # Layout
    W, H = 720, 220
    PAD_L, PAD_R, PAD_T, PAD_B = 70, 14, 18, 28
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    LANE_GAP = 8
    lane_h = (plot_h - LANE_GAP) / 2
    mac_y = PAD_T
    iphone_y = PAD_T + lane_h + LANE_GAP

    span = now_ts - cutoff_ts

    def x_of(ts: float) -> float:
        clamped = max(cutoff_ts, min(now_ts, ts))
        return PAD_L + (clamped - cutoff_ts) / span * plot_w

    def fmt_dur(s: float) -> str:
        s = int(round(s))
        if s >= 3600:
            return f"{s // 3600}h {(s % 3600) // 60}m"
        if s >= 60:
            return f"{s // 60}m {s % 60}s"
        return f"{s}s"

    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" '
        f'style="width: 100%; height: auto; max-height: 260px; '
        f'border: 1px solid var(--border); background: var(--bg-inset);">'
    )
    # Lane backgrounds + labels
    parts.append(
        f'<rect x="{PAD_L}" y="{mac_y}" width="{plot_w}" height="{lane_h}" fill="var(--chart-grid)" />'
    )
    parts.append(
        f'<rect x="{PAD_L}" y="{iphone_y}" width="{plot_w}" height="{lane_h}" fill="var(--chart-grid)" />'
    )
    parts.append(
        f'<text x="{PAD_L - 6}" y="{mac_y + lane_h / 2 + 4:.1f}" font-size="11" '
        f'text-anchor="end" fill="var(--chart-accent)" font-family="ui-monospace, monospace" font-weight="600">mac</text>'
    )
    parts.append(
        f'<text x="{PAD_L - 6}" y="{iphone_y + lane_h / 2 + 4:.1f}" font-size="11" '
        f'text-anchor="end" fill="#d97706" font-family="ui-monospace, monospace" font-weight="600">iphone</text>'
    )
    # Hour gridlines + labels (every 4h)
    for h in range(0, 25, 4):
        ts = cutoff_ts + h * 3600
        x = x_of(ts)
        parts.append(
            f'<line x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" y2="{H - PAD_B}" '
            f'stroke="var(--chart-grid)" stroke-width="1" />'
        )
        label_dt = cutoff + timedelta(hours=h)
        parts.append(
            f'<text x="{x:.1f}" y="{H - PAD_B + 14}" font-size="10" '
            f'text-anchor="middle" fill="var(--chart-muted)" font-family="ui-monospace, monospace">'
            f"{label_dt.strftime('%H:%M')}</text>"
        )
    # Session rectangles. Names come from screen_time_app_names (populated at
    # sync time); fall back to bundle_id when uncached. Never resolve at render
    # time — calling resolve_app_name() per row triggered mdfind / iTunes
    # lookups (~25ms each) that wedged the UI server.
    for s in sessions:
        x1 = x_of(s["start"])
        x2 = x_of(s["end"])
        w = max(1.0, x2 - x1)
        if s["platform"] == "mac":
            y, color = mac_y, "var(--chart-accent)"
        else:
            y, color = iphone_y, "#d97706"
        name = name_by_bundle.get(s["bundle"], s["bundle"])
        start_dt = datetime.fromtimestamp(s["start"])
        end_dt = datetime.fromtimestamp(s["end"])
        title = html.escape(
            f"{name} · {fmt_dur(s['dur'])} · "
            f"{start_dt.strftime('%H:%M:%S')}–{end_dt.strftime('%H:%M:%S')} · {s['platform']}"
        )
        parts.append(
            f'<rect x="{x1:.1f}" y="{y}" width="{w:.1f}" height="{lane_h}" '
            f'fill="{color}" fill-opacity="0.78" stroke="{color}" stroke-width="0.4" '
            f'shape-rendering="crispEdges">'
            f"<title>{title}</title></rect>"
        )
    parts.append("</svg>")

    total_mac = sum(s["dur"] for s in sessions if s["platform"] == "mac") / 3600
    total_phone = sum(s["dur"] for s in sessions if s["platform"] == "iphone") / 3600
    return (
        f'<p class="meta">{len(sessions)} merged sessions ({len(rows)} raw) · last 24h · '
        f'<span style="color:var(--chart-accent)">●</span> mac {round(total_mac, 1)}h · '
        f'<span style="color:#d97706">●</span> iphone {round(total_phone, 1)}h · '
        'hover any rectangle for app · data from Mosspath</p>'
        + "".join(parts)
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "device_flame_24h",
            "name": "Device Timeline (24h)",
            "description": "Flame-graph timeline of mac + iphone sessions over the last 24h, on a shared x-axis. Hover any rectangle for the app.",
            "render": render_device_flame_24h,
        },
        {
            "slug": "device_split_30d",
            "name": "Computer vs Phone (30d)",
            "description": "Mac vs iPhone hours per day, last 30 days. Hover any segment for the date and hours.",
            "render": render_device_split_30d,
        },
        {
            "slug": "top_apps_mac_30d",
            "name": "Top Mac Apps (30d, h/day)",
            "description": "Top 20 Mac apps by hours per day over the last 30 days. Hover for exact values.",
            "render": render_top_apps_mac_30d,
        },
        {
            "slug": "top_apps_phone_30d",
            "name": "Top Phone Apps (30d, h/day)",
            "description": "Top 20 iPhone apps by hours per day over the last 30 days. Hover for exact values.",
            "render": render_top_apps_phone_30d,
        },
        {
            "slug": "hourly_heatmap_7d",
            "name": "Hourly Heatmap (7d)",
            "description": "Daily-rhythm view: minutes of screen time per hour of day (Mac only).",
            "render": render_hourly_heatmap,
        },
    ]
