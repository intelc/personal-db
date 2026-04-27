import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_db.entities import sync_entities_from_yaml
from personal_db.tracker import Tracker

COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
DEFAULT_DB = Path("~/Library/Messages/chat.db").expanduser()


def _ns_to_iso(ns: int) -> str:
    # macOS sometimes stores seconds, sometimes nanoseconds; sniff scale
    seconds = ns / 1e9 if ns > 10**14 else float(ns)
    return (COCOA_EPOCH + timedelta(seconds=seconds)).isoformat()


def _resolve_db() -> Path:
    return Path(os.environ.get("PERSONAL_DB_IMESSAGE_DB", str(DEFAULT_DB)))


def backfill(t: Tracker, start, end) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    sync_entities_from_yaml(t.cfg)  # ensure aliases are loaded before resolution
    db = _resolve_db()
    if not db.exists():
        raise RuntimeError(f"chat.db not found at {db}")
    last = t.cursor.get(default="0")  # cursor is the max ROWID seen
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows_in = con.execute(
        """
        SELECT m.ROWID, h.id, m.text, m.is_from_me, m.date
        FROM message m LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE m.ROWID > ?
        ORDER BY m.ROWID
        """,
        (int(last),),
    ).fetchall()
    con.close()
    rows = []
    for rowid, handle, text, is_from_me, date in rows_in:
        person_id = t.resolve_person(handle, auto_create=True) if handle else None
        rows.append(
            {
                "id": rowid,
                "person_id": person_id,
                "handle": handle,
                "text": text or "",
                "is_from_me": int(is_from_me or 0),
                "sent_at": _ns_to_iso(date),
            }
        )
    if rows:
        t.upsert("imessage_messages", rows, key=["id"])
        t.cursor.set(str(rows[-1]["id"]))
    t.log.info("imessage: ingested %d rows", len(rows))
