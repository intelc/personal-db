"""Visualizations for chrome_history."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import heatmap, horizontal_bars
from personal_db.viz_helpers import connect_db as _connect
from personal_db.viz_helpers import meta


def render_top_domains(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return meta("no data")
    cutoff = (datetime.now() - timedelta(days=30)).date().isoformat()
    try:
        rows = con.execute(
            "SELECT domain, sum(duration_seconds)/3600.0 AS hours "
            "FROM chrome_visits "
            "WHERE duration_seconds > 0 AND visited_at >= ? AND domain != '' "
            "GROUP BY domain ORDER BY hours DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return meta("chrome_visits not synced yet")
    finally:
        con.close()
    items = [(d, round(h, 1)) for d, h in rows if h]
    return (
        '<p class="meta">last 30 days · top 20 domains by total dwell time</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{v}h")
    )


def render_hourly_heatmap(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return meta("no data")
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    try:
        rows = con.execute(
            "SELECT date(visited_at, 'localtime') AS d, "
            "       cast(strftime('%H', visited_at, 'localtime') AS INTEGER) AS h, "
            "       count(*) AS n "
            "FROM chrome_visits WHERE visited_at >= ? GROUP BY d, h",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return meta("chrome_visits not synced yet")
    finally:
        con.close()
    if not rows:
        return meta("no visits in the last 7 days")
    today = datetime.now().date()
    days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    by_day_hour: dict[tuple[str, int], int] = {(d, h): n for d, h, n in rows}
    grid = [[by_day_hour.get((d.isoformat(), h), 0) or None for h in range(24)] for d in days]
    row_labels = [d.strftime("%a %m-%d") for d in days]
    col_labels = [f"{h:02d}" for h in range(24)]
    return (
        '<p class="meta">visits per hour, last 7 days · darker = more</p>'
        + heatmap(grid, row_labels, col_labels)
    )


def metrics(cfg: Config) -> list[dict]:
    """Dashboard tile metrics: browsing hours today (vs 30d daily average)
    and today's top domain by dwell time.

    Fetches raw rows bounded to the last ~2 days (a plain range predicate
    SQLite can SEARCH via idx_chrome_visits_visited_at) and aggregates
    "today" in Python — grouping/filtering by date(visited_at, 'localtime')
    directly in SQL makes the planner pick a different index and scan the
    whole 99k-row table (measured ~35-100ms vs <2ms for the bounded fetch).
    """
    con = _connect(cfg)
    if not con:
        return []
    bound = (datetime.now() - timedelta(days=2)).isoformat()
    try:
        rows = con.execute(
            "SELECT domain, duration_seconds, visited_at FROM chrome_visits "
            "WHERE visited_at >= ? AND duration_seconds > 0",
            (bound,),
        ).fetchall()
        thirty_total = con.execute(
            "SELECT COALESCE(sum(duration_seconds), 0) FROM chrome_visits "
            "WHERE visited_at >= datetime('now', '-30 days') AND duration_seconds > 0"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    today = datetime.now().date()
    by_domain: dict[str, float] = {}
    today_seconds = 0.0
    for domain, dur, visited_at in rows:
        try:
            ts = datetime.fromisoformat(visited_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if ts.astimezone().date() != today:
            continue
        dur = dur or 0.0
        today_seconds += dur
        if domain:
            by_domain[domain] = by_domain.get(domain, 0.0) + dur

    today_hours = today_seconds / 3600.0
    avg_hours = (thirty_total or 0) / 3600.0 / 30.0

    delta = None
    good = None
    if avg_hours > 0.05:
        pct = (today_hours - avg_hours) / avg_hours * 100
        if abs(pct) >= 5:
            sign = "+" if pct >= 0 else ""
            delta = f"{sign}{pct:.0f}% vs 30d avg"
            good = False if pct >= 10 else (True if pct <= -10 else None)

    out = [
        {
            "label": "Browsing today",
            "value": f"{today_hours:.1f}h",
            "detail": None,
            "delta": delta,
            "good": good,
        }
    ]
    if by_domain:
        top_domain, top_secs = max(by_domain.items(), key=lambda kv: kv[1])
        out.append(
            {
                "label": "Top domain today",
                "value": top_domain,
                "detail": f"{top_secs / 3600.0:.1f}h",
                "delta": None,
                "good": None,
            }
        )
    return out


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "top_domains_30d",
            "name": "Top Domains (30d)",
            "description": "Top 20 domains by total dwell time over the last 30 days.",
            "render": render_top_domains,
        },
        {
            "slug": "hourly_heatmap_7d",
            "name": "Hourly Heatmap (7d)",
            "description": "Browsing intensity by day-of-week × hour-of-day.",
            "render": render_hourly_heatmap,
        },
    ]
