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
from datetime import UTC, datetime, timedelta
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


def _local_sessions(cfg: Config, cutoff_ts: float, now_ts: float) -> list[tuple]:
    """Return Mac sessions already imported by this tracker.

    ``knowledgeC.db`` is the required source for this tracker.  Mosspath is an
    optional companion that may contribute iPhone sessions, so visualizations
    must not make the Mac half of the page depend on its database existing.
    """
    con = _connect(cfg)
    if not con:
        return []
    cutoff_iso = datetime.fromtimestamp(cutoff_ts, UTC).isoformat()
    now_iso = datetime.fromtimestamp(now_ts, UTC).isoformat()
    try:
        rows = con.execute(
            "SELECT bundle_id, start_at, end_at, seconds "
            "FROM screen_time_app_usage "
            "WHERE end_at > ? AND start_at < ? ORDER BY start_at",
            (cutoff_iso, now_iso),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    sessions: list[tuple] = []
    for bundle_id, start_at, end_at, seconds in rows:
        try:
            start_ts = datetime.fromisoformat(start_at.replace("Z", "+00:00")).timestamp()
            end_ts = datetime.fromisoformat(end_at.replace("Z", "+00:00")).timestamp()
        except (AttributeError, TypeError, ValueError):
            continue
        if end_ts > start_ts:
            sessions.append(("mac", bundle_id, start_ts, end_ts, seconds or end_ts - start_ts))
    return sessions


def _local_top_apps(cfg: Config, cutoff: datetime) -> tuple[list[tuple], int]:
    """Top Mac apps and number of active Mac days over the requested window."""
    con = _connect(cfg)
    if not con:
        return [], 0
    try:
        rows = con.execute(
            "SELECT bundle_id, sum(seconds) / 3600.0 AS hours, count(*) AS sessions "
            "FROM screen_time_app_usage WHERE end_at > ? "
            "GROUP BY bundle_id ORDER BY hours DESC LIMIT 20",
            (cutoff.astimezone(UTC).isoformat(),),
        ).fetchall()
        active_days = con.execute(
            "SELECT count(DISTINCT date(start_at, 'localtime')) "
            "FROM screen_time_app_usage WHERE end_at > ?",
            (cutoff.astimezone(UTC).isoformat(),),
        ).fetchone()[0]
        return rows, active_days or 0
    except sqlite3.OperationalError:
        return [], 0
    finally:
        con.close()


def _latest_local_usage(cfg: Config) -> datetime | None:
    """Most recent imported Mac session, for an actionable empty-state hint."""
    con = _connect(cfg)
    if not con:
        return None
    try:
        row = con.execute("SELECT max(end_at) FROM screen_time_app_usage").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None


def _mac_freshness_hint(cfg: Config) -> str:
    """Explain when a successful sync has no recent source rows to import."""
    latest = _latest_local_usage(cfg)
    if not latest:
        return ""
    if latest.astimezone(UTC) >= datetime.now(UTC) - timedelta(hours=24):
        return ""
    return f" · latest Mac usage ended {latest.astimezone().strftime('%Y-%m-%d %H:%M %Z')}"


def _mosspath_sessions(platform: str, cutoff_ts: float, now_ts: float) -> list[tuple]:
    """Read optional Mosspath sessions for one platform, if available."""
    if not _MOSSPATH_DB.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{_MOSSPATH_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT platform, bundle_id, start_timestamp, end_timestamp, duration_seconds "
            "FROM screen_time_sessions "
            "WHERE platform = ? AND end_timestamp > ? AND start_timestamp < ? "
            "ORDER BY start_timestamp",
            (platform, cutoff_ts, now_ts),
        ).fetchall()
        con.close()
        return rows
    except sqlite3.OperationalError:
        return []


def _phone_source_status() -> str:
    """Human-readable state of optional Mosspath iPhone capture."""
    if not _MOSSPATH_DB.exists():
        return "iPhone not connected"
    try:
        con = sqlite3.connect(f"file:{_MOSSPATH_DB}?mode=ro", uri=True)
        row = con.execute(
            "SELECT max(end_timestamp) FROM screen_time_sessions WHERE platform = 'iphone'"
        ).fetchone()
        con.close()
    except sqlite3.OperationalError:
        return "iPhone source unavailable"
    if not row or row[0] is None:
        return "no iPhone sessions captured yet"
    latest = datetime.fromtimestamp(row[0], UTC).astimezone()
    if latest < datetime.now().astimezone() - timedelta(hours=24):
        return f"latest iPhone session ended {latest.strftime('%Y-%m-%d %H:%M %Z')}"
    return "iPhone data from Mosspath"


def _render_top_apps_for_platform(cfg: Config, platform: str, label: str) -> str:
    """Top apps over last 30 days, in hours/day average.

    Mac usage comes from this tracker's own imported table.  Only iPhone
    usage is optional Mosspath data.
    """
    if platform == "mac":
        rows, days_with_data = _local_top_apps(cfg, datetime.now() - timedelta(days=30))
        if not rows or not days_with_data:
            return '<p class="meta">no Mac data in last 30 days</p>'

        name_by_bundle = _load_name_cache(cfg)
        items = [
            (f"{name_by_bundle.get(bundle_id, bundle_id)} ({sessions} sess)", round(hours / days_with_data, 2))
            for bundle_id, hours, sessions in rows
            if hours
        ]
        return (
            f'<p class="meta">top 20 Mac apps · last 30 days · '
            f'hours per day, averaged over {days_with_data} active days · '
            'session count in parens · data from macOS Screen Time</p>'
            + horizontal_bars(items, value_fmt=lambda v: f"{v} h/day")
        )

    if not _MOSSPATH_DB.exists():
        return (
            '<p class="meta">no iPhone data in last 30 days — this tracker imports Mac usage '
            'from macOS; install Mosspath to add iPhone sessions.</p>'
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
        return f'<p class="meta">cannot read optional iPhone data: {html.escape(str(e))}</p>'

    if not rows or not days_with_data:
        return f'<p class="meta">no {label} data in last 30 days · {_phone_source_status()}</p>'

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
    """Mac vs iPhone hours per day, sourced independently when possible."""
    now = datetime.now()
    cutoff = now - timedelta(days=30)
    cutoff_ts = cutoff.timestamp()
    now_ts = now.timestamp()
    mac_sessions = _local_sessions(cfg, cutoff_ts, now_ts)
    phone_sessions = _mosspath_sessions("iphone", cutoff_ts, now_ts)
    if not mac_sessions and not phone_sessions:
        return (
            '<p class="meta">no Mac or iPhone screen-time sessions in the last 30 days'
            f'{_mac_freshness_hint(cfg)} · {_phone_source_status()}.</p>'
        )

    by: dict[tuple[str, str], float] = {}
    for platform, _bundle, start_ts, _end_ts, duration in mac_sessions + phone_sessions:
        day = datetime.fromtimestamp(start_ts).date().isoformat()
        by[(day, platform)] = by.get((day, platform), 0) + (duration or 0) / 3600.0

    today = now.date()
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
        'Mac data from macOS Screen Time'
        + _mac_freshness_hint(cfg)
        + f' · {_phone_source_status()}</p>'
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
    now = datetime.now()
    cutoff = now - timedelta(hours=24)
    cutoff_ts = cutoff.timestamp()
    now_ts = now.timestamp()
    rows = _local_sessions(cfg, cutoff_ts, now_ts)
    rows.extend(_mosspath_sessions("iphone", cutoff_ts, now_ts))

    if not rows:
        return (
            '<p class="meta">no Mac or iPhone screen-time sessions in the last 24 hours'
            f'{_mac_freshness_hint(cfg)} · {_phone_source_status()}.</p>'
        )

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
        'hover any rectangle for app · Mac data from macOS Screen Time'
        + f' · {_phone_source_status()}</p>'
        + "".join(parts)
    )


def metrics(cfg: Config) -> list[dict]:
    """Dashboard tile metrics: Mac hours today, iPhone hours today (vs 30d
    avg), and today's top Mac app. Never raises — missing tables/files just
    shrink the returned list."""
    out: list[dict] = []

    con = _connect(cfg)
    if con:
        # Bound the scan to the last 2 local days so the SQLite planner can
        # SEARCH the start_at index instead of scanning the whole table; the
        # date(...,'localtime') predicate then only evaluates that small slice.
        bound = (datetime.now() - timedelta(days=2)).isoformat()
        try:
            mac_hours = con.execute(
                "SELECT COALESCE(sum(seconds), 0) / 3600.0 FROM screen_time_app_usage "
                "WHERE start_at >= ? AND date(start_at, 'localtime') = date('now', 'localtime')",
                (bound,),
            ).fetchone()[0]
            top_row = con.execute(
                "SELECT bundle_id, sum(seconds) / 3600.0 AS hours FROM screen_time_app_usage "
                "WHERE start_at >= ? AND date(start_at, 'localtime') = date('now', 'localtime') "
                "GROUP BY bundle_id ORDER BY hours DESC LIMIT 1",
                (bound,),
            ).fetchone()
        except sqlite3.OperationalError:
            mac_hours, top_row = None, None
        con.close()

        if mac_hours is not None:
            out.append(
                {
                    "label": "Mac today",
                    "value": f"{mac_hours:.1f}h",
                    "detail": None,
                    "delta": None,
                    "good": None,
                }
            )
        if top_row and top_row[1]:
            name_by_bundle = _load_name_cache(cfg)
            bundle_id, hours = top_row
            out.append(
                {
                    "label": "Top app today",
                    "value": name_by_bundle.get(bundle_id, bundle_id),
                    "detail": f"{hours:.1f}h",
                    "delta": None,
                    "good": None,
                }
            )

    if _MOSSPATH_DB.exists():
        try:
            mcon = sqlite3.connect(f"file:{_MOSSPATH_DB}?mode=ro", uri=True)
            now = datetime.now()
            today_cutoff_ts = int((now - timedelta(days=2)).timestamp())
            rows = mcon.execute(
                "SELECT start_timestamp, duration_seconds FROM screen_time_sessions "
                "WHERE platform = 'iphone' AND start_timestamp >= ?",
                (today_cutoff_ts,),
            ).fetchall()
            thirty_cutoff_ts = (now - timedelta(days=30)).timestamp()
            thirty_total = mcon.execute(
                "SELECT COALESCE(sum(duration_seconds), 0) FROM screen_time_sessions "
                "WHERE platform = 'iphone' AND start_timestamp >= ?",
                (thirty_cutoff_ts,),
            ).fetchone()[0]
            mcon.close()
        except sqlite3.OperationalError:
            rows, thirty_total = [], 0.0

        today_date = now.date()
        today_seconds = sum(
            dur or 0
            for ts, dur in rows
            if datetime.fromtimestamp(ts).date() == today_date
        )
        iphone_hours = today_seconds / 3600.0
        avg_hours = (thirty_total or 0) / 3600.0 / 30.0

        delta = None
        good = None
        if avg_hours > 0.05:
            pct = (iphone_hours - avg_hours) / avg_hours * 100
            if abs(pct) >= 5:
                sign = "+" if pct >= 0 else ""
                delta = f"{sign}{pct:.0f}% vs 30d avg"
                # More phone screen time is not a win.
                good = False if pct >= 10 else (True if pct <= -10 else None)

        out.append(
            {
                "label": "iPhone today",
                "value": f"{iphone_hours:.1f}h",
                "detail": None,
                "delta": delta,
                "good": good,
            }
        )

    return out[:4]


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
