import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_db.app_names import resolve_app_name
from personal_db.db import connect
from personal_db.tracker import Tracker

# Apple Cocoa epoch: 2001-01-01 UTC
COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
DEFAULT_DB = Path("~/Library/Application Support/Knowledge/knowledgeC.db").expanduser()
MOSSPATH_DB = Path("~/Library/Application Support/Mosspath/store/events.sqlite").expanduser()


def _cocoa_to_iso(seconds: float) -> str:
    return (COCOA_EPOCH + timedelta(seconds=seconds)).isoformat()


def _resolve_db_path() -> Path:
    return Path(os.environ.get("PERSONAL_DB_SCREEN_TIME_DB", str(DEFAULT_DB)))


def backfill(t: Tracker, start, end) -> None:
    sync(t)  # full read; UNIQUE constraint handles dedup


def sync(t: Tracker) -> None:
    db = _resolve_db_path()
    if not db.exists():
        raise RuntimeError(f"knowledgeC.db not found at {db}")
    cursor_iso = t.cursor.get(default="2020-01-01T00:00:00+00:00")
    # ZSTARTDATE is Cocoa-epoch seconds. Convert cursor back to compare.
    cursor_dt = datetime.fromisoformat(cursor_iso)
    cursor_cocoa = (cursor_dt - COCOA_EPOCH).total_seconds()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows_in = con.execute(
        """
        SELECT ZVALUESTRING, ZSTARTDATE, ZENDDATE
        FROM ZOBJECT
        WHERE ZSTREAMNAME='/app/usage' AND ZSTARTDATE > ?
        ORDER BY ZSTARTDATE
    """,
        (cursor_cocoa,),
    ).fetchall()
    con.close()
    rows = []
    for bundle, s, e in rows_in:
        if not bundle or s is None or e is None:
            continue
        rows.append(
            {
                "bundle_id": bundle,
                "start_at": _cocoa_to_iso(s),
                "end_at": _cocoa_to_iso(e),
                "seconds": int(e - s),
            }
        )
    if rows:
        t.upsert("screen_time_app_usage", rows, key=["bundle_id", "start_at"])
        t.cursor.set(rows[-1]["start_at"])
    t.log.info("screen_time: ingested %d rows", len(rows))
    _populate_app_name_cache(t)


def _populate_app_name_cache(t: Tracker) -> None:
    """Resolve any new bundle_ids seen locally or in Mosspath sessions into screen_time_app_names.

    Renders read from this cache only; resolution stays on the sync path so the
    UI never blocks on mdfind/iTunes lookups.
    """
    con = connect(t.cfg.db_path)
    try:
        bundles: set[str] = {
            row[0]
            for row in con.execute("SELECT DISTINCT bundle_id FROM screen_time_app_usage")
        }
        if MOSSPATH_DB.exists():
            try:
                mp = sqlite3.connect(f"file:{MOSSPATH_DB}?mode=ro", uri=True)
                bundles.update(
                    row[0]
                    for row in mp.execute(
                        "SELECT DISTINCT bundle_id FROM screen_time_sessions "
                        "WHERE bundle_id IS NOT NULL"
                    )
                )
                mp.close()
            except sqlite3.OperationalError:
                pass

        cached = {
            row[0] for row in con.execute("SELECT bundle_id FROM screen_time_app_names")
        }
        missing = bundles - cached
        if not missing:
            return

        cache_path = t.cfg.state_dir / "screen_time_app_name_cache.json"
        now_iso = datetime.now(UTC).isoformat()
        for bundle in missing:
            name = resolve_app_name(bundle, cache_path=cache_path)
            con.execute(
                "INSERT OR REPLACE INTO screen_time_app_names "
                "(bundle_id, app_name, resolved_at) VALUES (?, ?, ?)",
                (bundle, name, now_iso),
            )
        con.commit()
        t.log.info("screen_time: cached %d new app names", len(missing))
    finally:
        con.close()
