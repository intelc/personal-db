"""Visualizations for the contacts tracker.

Contacts is mostly a JOIN target (used by imessage, etc.), so the dedicated
viz here are reference-flavored: an at-a-glance summary and a "who has the
most ways to reach me" list.
"""

from __future__ import annotations

import sqlite3
from html import escape

from personal_db.config import Config
from personal_db.ui.charts import horizontal_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_summary(cfg: Config) -> str:
    """Top-line counts: contacts / phones / emails / per-source breakdown."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    try:
        n_contacts = con.execute("SELECT count(*) FROM contacts").fetchone()[0]
        n_phones = con.execute(
            "SELECT count(*) FROM contact_handles WHERE kind='phone'"
        ).fetchone()[0]
        n_emails = con.execute(
            "SELECT count(*) FROM contact_handles WHERE kind='email'"
        ).fetchone()[0]
        per_source = con.execute(
            "SELECT source, count(*) FROM contacts GROUP BY source ORDER BY 2 DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">contacts not synced yet</p>'
    finally:
        con.close()
    if n_contacts == 0:
        return '<p class="meta">no contacts synced yet — run <code>personal-db sync contacts</code></p>'
    sources_html = "".join(
        f"<tr><td>{escape(src)}</td><td>{n}</td></tr>" for src, n in per_source
    )
    return (
        '<table class="health">'
        "<tbody>"
        f"<tr><td>contacts</td><td><strong>{n_contacts:,}</strong></td></tr>"
        f"<tr><td>phone numbers</td><td>{n_phones:,}</td></tr>"
        f"<tr><td>email addresses</td><td>{n_emails:,}</td></tr>"
        "</tbody></table>"
        '<p class="meta" style="margin-top: 16px;">per source:</p>'
        f'<table class="health"><tbody>{sources_html}</tbody></table>'
    )


def render_top_handles(cfg: Config) -> str:
    """Contacts with the most handles (phones + emails). Usually family or
    long-term contacts whose info accreted multiple ways."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    try:
        rows = con.execute(
            "SELECT c.display_name, count(*) AS n "
            "FROM contact_handles ch "
            "JOIN contacts c ON c.contact_id = ch.contact_id "
            "GROUP BY ch.contact_id, c.display_name "
            "HAVING n >= 2 "
            "ORDER BY n DESC, c.display_name LIMIT 20"
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">contacts not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no contacts with multiple handles</p>'
    items = [(name, n) for name, n in rows]
    return (
        '<p class="meta">contacts with the most handles (phones + emails)</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{int(v)}")
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "summary",
            "name": "Summary",
            "description": "Total counts and per-source breakdown.",
            "render": render_summary,
        },
        {
            "slug": "top_handles",
            "name": "Most Handles",
            "description": "Contacts with the most phone numbers + email addresses.",
            "render": render_top_handles,
        },
    ]
