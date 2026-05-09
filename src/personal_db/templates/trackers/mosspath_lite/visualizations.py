"""Visualizations for Mosspath Lite computer activity."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from html import escape

from personal_db.config import Config
from personal_db.ui.charts import heatmap, horizontal_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        con = sqlite3.connect(cfg.db_path)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


def _since_24h() -> str:
    """ISO-8601 UTC cutoff for the past 24 hours, comparable to stored timestamps."""
    return (datetime.now(UTC) - timedelta(hours=24)).isoformat()


def _short_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%H:%M")
    except ValueError:
        return value[11:16]


def _duration(started_at: str, ended_at: str) -> str:
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
    except ValueError:
        return ""
    minutes = max(0, int((end - start).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"


def _json_count(value: str | None) -> int:
    if not value:
        return 0
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def render_recent_story(cfg: Config) -> str:
    """High-signal rolling story: routine answers + work episodes from the last 24h."""

    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'
    cutoff = _since_24h()
    try:
        answers = con.execute(
            """
            SELECT question_title, answer_markdown, confidence, started_at, ended_at
            FROM mosspath_lite_routine_answers
            WHERE started_at >= ?
            ORDER BY ended_at DESC
            """,
            (cutoff,),
        ).fetchall()
        episodes = con.execute(
            """
            SELECT started_at, ended_at, title, what, outcome, confidence,
                   source_session_ids_json
            FROM mosspath_lite_work_episodes
            WHERE started_at >= ?
            ORDER BY started_at DESC
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">mosspath_lite tables not synced yet</p>'
    finally:
        con.close()

    if not answers and not episodes:
        return '<p class="meta">no Mosspath Lite episodes or routine answers in the past 24h</p>'

    parts: list[str] = [_story_table_style()]
    if answers:
        answer_blocks = []
        for row in answers:
            answer_blocks.append(
                '<div class="recent-rows-wrap">'
                '<table class="recent-rows"><tbody>'
                f'<tr><th>{escape(row["question_title"] or "Routine answer")}</th></tr>'
                f'<tr><td>{escape(row["answer_markdown"] or "")}</td></tr>'
                '</tbody></table></div>'
                f'<p class="meta">{_short_time(row["started_at"])}-{_short_time(row["ended_at"])}'
                f' · confidence {row["confidence"] or 0:.2f}</p>'
            )
        parts.append("".join(answer_blocks))

    if episodes:
        rows = []
        for row in episodes:
            sessions = _json_count(row["source_session_ids_json"])
            what = row["what"] or ""
            outcome = row["outcome"] or ""
            detail = outcome or what
            rows.append(
                "<tr>"
                f'<td class="ms-time">{escape(_short_time(row["started_at"]))}<br>'
                f'<span class="meta">{escape(_duration(row["started_at"], row["ended_at"]))}</span></td>'
                f'<td class="ms-episode"><strong>{escape(row["title"] or "Untitled episode")}</strong><br>'
                f'{escape(detail)}</td>'
                f'<td class="ms-num">{sessions}</td>'
                f'<td class="ms-num">{row["confidence"] or 0:.2f}</td>'
                "</tr>"
            )
        parts.append(
            '<p class="meta">past 24h · semantic work episodes, newest first</p>'
            '<div class="recent-rows-wrap mosspath-story-wrap"><table class="recent-rows mosspath-story">'
            '<colgroup><col class="ms-col-time"><col class="ms-col-episode">'
            '<col class="ms-col-sessions"><col class="ms-col-conf"></colgroup>'
            '<thead><tr><th>time</th><th>episode</th><th>sessions</th><th>conf</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
        )
    return "".join(parts)


def _story_table_style() -> str:
    """Inline override because deployed dashboard CSS may lag the tracker file."""
    return """
    <style>
      .mosspath-story-wrap {
        width: 100% !important;
        max-height: 360px !important;
        overflow-y: auto !important;
        overflow-x: visible !important;
      }
      table.mosspath-story {
        width: 100% !important;
        min-width: 100% !important;
        table-layout: fixed !important;
        white-space: normal !important;
      }
      table.mosspath-story .ms-col-time { width: 96px; }
      table.mosspath-story .ms-col-sessions { width: 96px; }
      table.mosspath-story .ms-col-conf { width: 80px; }
      table.mosspath-story th,
      table.mosspath-story td {
        max-width: none !important;
        overflow: visible !important;
        text-overflow: clip !important;
        white-space: normal !important;
        overflow-wrap: anywhere !important;
      }
      table.mosspath-story td.ms-episode {
        line-height: 1.45;
      }
      table.mosspath-story thead th {
        position: sticky;
        top: 0;
        z-index: 1;
      }
    </style>
    """


def render_recent_apps_domains(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'
    cutoff = _since_24h()
    try:
        app_rows = con.execute(
            """
            SELECT coalesce(app_name, '(unknown)') AS label, count(*) AS n
            FROM mosspath_lite_events
            WHERE timestamp >= ?
            GROUP BY label
            ORDER BY n DESC
            LIMIT 12
            """,
            (cutoff,),
        ).fetchall()
        domain_rows = con.execute(
            """
            SELECT browser_domain AS label, count(*) AS n
            FROM mosspath_lite_events
            WHERE timestamp >= ?
              AND browser_domain IS NOT NULL
              AND browser_domain != ''
            GROUP BY label
            ORDER BY n DESC
            LIMIT 12
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">mosspath_lite_events not synced yet</p>'
    finally:
        con.close()

    app_items = [(row["label"], row["n"]) for row in app_rows]
    domain_items = [(row["label"], row["n"]) for row in domain_rows]
    return (
        '<p class="meta">past 24h · event counts by app and browser domain</p>'
        '<h3 class="block-h">APPS</h3>'
        + horizontal_bars(app_items, value_fmt=lambda v: f"{int(v)}")
        + '<h3 class="block-h">DOMAINS</h3>'
        + horizontal_bars(domain_items, value_fmt=lambda v: f"{int(v)}")
    )


def render_activity_heatmap(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">database not initialized yet</p>'
    cutoff = (datetime.now().astimezone() - timedelta(days=6)).date().isoformat()
    try:
        rows = con.execute(
            """
            SELECT date(timestamp, 'localtime') AS d,
                   cast(strftime('%H', timestamp, 'localtime') AS INTEGER) AS h,
                   count(*) AS n
            FROM mosspath_lite_events
            WHERE date(timestamp, 'localtime') >= ?
            GROUP BY d, h
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">mosspath_lite_events not synced yet</p>'
    finally:
        con.close()

    if not rows:
        return '<p class="meta">no Mosspath Lite events in the last 7 days</p>'

    today = datetime.now().astimezone().date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    by_day_hour = {(row["d"], row["h"]): row["n"] for row in rows}
    grid = [[by_day_hour.get((d.isoformat(), h), 0) or None for h in range(24)] for d in days]
    row_labels = [d.strftime("%a %m-%d") for d in days]
    col_labels = [f"{h:02d}" for h in range(24)]
    return (
        '<p class="meta">events per hour, last 7 days · darker = more captured activity</p>'
        + heatmap(grid, row_labels, col_labels)
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "00_recent_story",
            "name": "Recent Story (24h)",
            "description": "Routine answers and semantic work episodes from the past 24 hours.",
            "render": render_recent_story,
        },
        {
            "slug": "01_recent_apps_domains",
            "name": "Recent Apps & Domains (24h)",
            "description": "Top apps and browser domains by captured event count over the past 24 hours.",
            "render": render_recent_apps_domains,
        },
        {
            "slug": "02_activity_heatmap_7d",
            "name": "Activity Heatmap (7d)",
            "description": "Mosspath Lite event intensity by day and hour.",
            "render": render_activity_heatmap,
        },
    ]
