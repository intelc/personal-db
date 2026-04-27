"""Visualizations for the life_context tracker."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from html import escape

from personal_db.config import Config

_QUICK_STATES = ["well", "sick", "recovering", "traveling",
                 "focused", "distracted", "system_event"]


def _local_today() -> date:
    return datetime.now().astimezone().date()


def render_recent_with_log(cfg: Config) -> str:
    today = _local_today()
    today_iso = today.isoformat()
    cutoff = (today - timedelta(days=13)).isoformat()

    today_rows: list[dict] = []
    recent_rows: list[dict] = []
    try:
        con = sqlite3.connect(cfg.db_path)
        try:
            today_rows = [
                {"state": r[0], "note": r[1]}
                for r in con.execute(
                    "SELECT state, note FROM life_context WHERE date = ? ORDER BY id ASC",
                    (today_iso,),
                ).fetchall()
            ]
            recent_rows = [
                {"date": r[0], "state": r[1], "note": r[2]}
                for r in con.execute(
                    "SELECT date, state, note FROM life_context "
                    "WHERE date >= ? ORDER BY date DESC, id DESC",
                    (cutoff,),
                ).fetchall()
            ]
        finally:
            con.close()
    except sqlite3.OperationalError:
        pass

    if today_rows:
        today_html = '<ul class="lc-today">'
        for e in today_rows:
            state_html = (
                f'<span class="lc-state">{escape(e["state"])}</span>' if e["state"] else ""
            )
            note_html = f'<span class="lc-note">{escape(e["note"])}</span>' if e["note"] else ""
            today_html += f"<li>{state_html}{note_html}</li>"
        today_html += "</ul>"
    else:
        today_html = '<p class="meta">nothing logged for today</p>'

    state_options = "".join(
        f'<option value="{s}">{s}</option>' for s in _QUICK_STATES
    )
    form_html = (
        '<form action="/log_life_context" method="post" class="lc-form">'
        f'<label>from <input type="date" name="start_date" value="{today_iso}" required></label>'
        '<label>to <input type="date" name="end_date" placeholder="(optional)"></label>'
        '<label>state <select name="state">'
        f'<option value=""></option>{state_options}'
        "</select></label>"
        '<label class="grow">note '
        '<input type="text" name="note" placeholder="free text"></label>'
        '<button type="submit">log</button>'
        "</form>"
    )

    if recent_rows:
        recent_table = '<details class="lc-recent">'
        recent_table += f"<summary>last 14 days ({len(recent_rows)} entries)</summary>"
        recent_table += '<table class="recent"><tbody>'
        for r in recent_rows:
            state_html = (
                f'<span class="lc-state">{escape(r["state"])}</span>' if r["state"] else ""
            )
            note_html = escape(r["note"] or "")
            recent_table += (
                f'<tr><td class="lc-date">{escape(r["date"])}</td>'
                f"<td>{state_html}</td>"
                f'<td class="lc-note-cell">{note_html}</td></tr>'
            )
        recent_table += "</tbody></table></details>"
    else:
        recent_table = ""

    return today_html + form_html + recent_table


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "recent_with_log",
            "name": "Diary",
            "description": "Today's entries, an inline log form, and the last 14 days.",
            "render": render_recent_with_log,
        },
    ]
