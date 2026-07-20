"""Visualizations for imessage_messages: top contacts + word cloud."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from personal_db.config import Config
from personal_db.handle_norm import normalize_handle
from personal_db.ui.charts import horizontal_bars, word_cloud

# A pragmatic English stopword list — short enough that the word cloud doesn't
# devolve into "the/and/you/i" noise. Plus iMessage-specific noise: tapbacks,
# url tokens, and very short tokens.
_STOPWORDS = {
    # articles, pronouns, common particles
    "the", "a", "an", "i", "you", "me", "my", "your", "we", "us", "our", "they",
    "them", "their", "he", "she", "it", "its", "this", "that", "these", "those",
    "to", "of", "for", "on", "in", "at", "by", "from", "with", "as", "but", "or",
    "so", "if", "is", "am", "are", "was", "were", "be", "been", "being", "do",
    "does", "did", "have", "has", "had", "will", "would", "should", "could",
    "can", "may", "might", "shall", "and", "not", "no", "yes", "yeah", "yep",
    "ok", "okay", "ya", "lol", "haha", "lmao", "u", "ur", "im", "dont", "thats",
    "its", "wasnt", "didnt", "isnt", "cant", "wont", "ill", "youll", "weve",
    "youre", "theyre", "youve", "ive",
    # filler / fillers
    "just", "now", "then", "than", "very", "also", "really", "actually", "kind",
    "sort", "well", "still", "even", "much", "any", "some", "all", "one", "two",
    "want", "need", "get", "got", "go", "going", "gonna", "wanna", "let", "see",
    "know", "think", "say", "said", "tell", "ask", "good", "great", "thanks",
    "thank", "sure", "fine", "right", "back", "way", "out", "up", "down", "over",
    "off", "about", "what", "when", "where", "who", "why", "how", "which",
    # iMessage-specific noise
    "image", "video", "attachment", "tapback", "liked", "loved", "emphasized",
    "questioned", "laughed", "disliked",
}

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z']{2,}")
_URL_RE = re.compile(r"https?://\S+")


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def _load_contact_lookup(con: sqlite3.Connection) -> dict[str, str]:
    """Build {normalized_handle → contact display_name} from the contacts tracker.

    Returns empty dict if contacts isn't installed/synced — the viz then falls
    back to raw handles. This is what makes the contacts dependency *optional*.
    """
    try:
        rows = con.execute(
            "SELECT ch.normalized, c.display_name "
            "FROM contact_handles ch "
            "JOIN contacts c ON c.contact_id = ch.contact_id"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {normalized: name for normalized, name in rows if normalized and name}


def render_top_contacts(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        contact_lookup = _load_contact_lookup(con)
        # Group by raw handle first; we re-aggregate by display_name in Python so
        # multiple handles for the same person collapse into one row.
        rows = con.execute(
            "SELECT m.handle, count(*) AS n "
            "FROM imessage_messages m "
            "WHERE m.sent_at >= ? AND m.is_from_me = 0 AND m.handle IS NOT NULL "
            "GROUP BY m.handle",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">imessage_messages not synced yet</p>'
    finally:
        con.close()

    counts: Counter[str] = Counter()
    resolved_count = 0
    for handle, n in rows:
        norm = normalize_handle(handle)
        name = contact_lookup.get(norm)
        if name:
            counts[name] += n
            resolved_count += n
        else:
            counts[handle or "(unknown)"] += n

    if not counts:
        return '<p class="meta">no inbound messages in the last 30 days</p>'

    items = counts.most_common(20)
    total = sum(counts.values())
    if contact_lookup:
        coverage = f"{(resolved_count / total) * 100:.0f}%" if total else "0%"
        meta = (
            f"last 30 days · top 20 contacts by inbound message count · "
            f"{coverage} of messages resolved to a contact name"
        )
    else:
        meta = (
            "last 30 days · top 20 contacts by inbound message count · "
            "showing raw handles (install <code>contacts</code> tracker for names)"
        )
    return (
        f'<p class="meta">{meta}</p>'
        + horizontal_bars(items, value_fmt=lambda v: f"{int(v)}")
    )


def render_word_cloud(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        rows = con.execute(
            "SELECT text FROM imessage_messages "
            "WHERE sent_at >= ? AND text IS NOT NULL AND text != ''",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">imessage_messages not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no messages in the last 30 days</p>'

    counter: Counter[str] = Counter()
    for (text,) in rows:
        if not text:
            continue
        cleaned = _URL_RE.sub("", text.lower())
        for tok in _TOKEN_RE.findall(cleaned):
            tok = tok.strip("'")
            if tok in _STOPWORDS or len(tok) < 3:
                continue
            counter[tok] += 1

    if not counter:
        return '<p class="meta">no significant words after filtering</p>'
    top = counter.most_common(50)
    return (
        '<p class="meta">last 30 days · top 50 words (stopwords + URLs filtered) · '
        f"{sum(counter.values()):,} tokens total</p>"
        + word_cloud(top)
    )


def metrics(cfg: Config) -> list[dict]:
    """Dashboard tile metrics: messages today, messages in the last 7 days
    (vs the previous 7 days), and the top inbound contact over 30 days."""
    con = _connect(cfg)
    if not con:
        return []
    out: list[dict] = []

    # Bound the scan to the last 2 local days so SQLite can SEARCH the
    # sent_at index (a plain range predicate) instead of scanning the whole
    # 188k-row table just to evaluate date(sent_at, 'localtime').
    two_day_bound = (datetime.now() - timedelta(days=2)).isoformat()
    try:
        today_count = con.execute(
            "SELECT count(*) FROM imessage_messages "
            "WHERE sent_at >= ? AND date(sent_at, 'localtime') = date('now', 'localtime')",
            (two_day_bound,),
        ).fetchone()[0]
        row = con.execute(
            "SELECT "
            "  sum(CASE WHEN sent_at >= datetime('now', '-7 days') THEN 1 ELSE 0 END), "
            "  sum(CASE WHEN sent_at >= datetime('now', '-14 days') "
            "           AND sent_at < datetime('now', '-7 days') THEN 1 ELSE 0 END) "
            "FROM imessage_messages WHERE sent_at >= datetime('now', '-14 days')",
        ).fetchone()
        contact_lookup = _load_contact_lookup(con)
        top_rows = con.execute(
            "SELECT handle, count(*) AS n FROM imessage_messages "
            "WHERE sent_at >= datetime('now', '-30 days') "
            "  AND is_from_me = 0 AND handle IS NOT NULL "
            "GROUP BY handle ORDER BY n DESC LIMIT 20",
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    out.append(
        {
            "label": "Messages today",
            "value": str(int(today_count or 0)),
            "detail": None,
            "delta": None,
            "good": None,
        }
    )

    last7, prev7 = (row or (0, 0))
    last7 = last7 or 0
    prev7 = prev7 or 0
    delta = None
    # Percentages off a tiny baseline are more confusing than useful, so
    # fall back to an absolute-count delta below a small-baseline threshold.
    if prev7 >= 5:
        pct = (last7 - prev7) / prev7 * 100
        sign = "+" if pct >= 0 else ""
        delta = f"{sign}{pct:.0f}% vs prior 7d"
    elif last7 != prev7:
        diff = last7 - prev7
        sign = "+" if diff >= 0 else ""
        delta = f"{sign}{int(diff)} vs prior 7d"
    out.append(
        {
            "label": "Messages (7d)",
            "value": str(int(last7)),
            "detail": None,
            "delta": delta,
            "good": None,  # message volume isn't inherently good or bad
        }
    )

    counts: Counter[str] = Counter()
    for handle, n in top_rows:
        norm = normalize_handle(handle)
        name = contact_lookup.get(norm) if contact_lookup else None
        counts[name or handle] += n
    if counts:
        top_name, top_n = counts.most_common(1)[0]
        out.append(
            {
                "label": "Top contact (30d)",
                "value": top_name,
                "detail": f"{top_n} inbound",
                "delta": None,
                "good": None,
            }
        )

    return out[:4]


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "top_contacts_30d",
            "name": "Top Contacts (30d)",
            "description": "Inbound message count per contact over the last 30 days.",
            "render": render_top_contacts,
        },
        {
            "slug": "word_cloud_30d",
            "name": "Word Cloud (30d)",
            "description": "Most-frequent words in the last 30 days of messages.",
            "render": render_word_cloud,
        },
    ]
