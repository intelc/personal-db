"""Visualizations for imessage_messages: top contacts + word cloud."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from personal_db.config import Config
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


def render_top_contacts(cfg: Config) -> str:
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        rows = con.execute(
            "SELECT coalesce(p.display_name, m.handle, '?') AS who, count(*) AS n "
            "FROM imessage_messages m "
            "LEFT JOIN people p ON p.person_id = m.person_id "
            "WHERE m.sent_at >= ? AND m.is_from_me = 0 "
            "GROUP BY who ORDER BY n DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">imessage_messages not synced yet</p>'
    finally:
        con.close()
    items = [(who, n) for who, n in rows if n]
    return (
        '<p class="meta">last 30 days · top 20 contacts by inbound message count</p>'
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
