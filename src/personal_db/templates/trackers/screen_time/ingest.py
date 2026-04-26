import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_db.tracker import Tracker

# Apple Cocoa epoch: 2001-01-01 UTC
COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
DEFAULT_DB = Path("~/Library/Application Support/Knowledge/knowledgeC.db").expanduser()


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
