"""Chrome history connector — per-visit rows from local History SQLite."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from personal_db.tracker import Tracker

# Chrome stores time as microseconds since 1601-01-01 UTC (WebKit epoch).
_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)


def _profiles_root() -> Path:
    return Path(
        os.environ.get("CHROME_PROFILES_DIR") or "~/Library/Application Support/Google/Chrome"
    ).expanduser()


def _list_profiles(base: Path) -> list[Path]:
    """Profile directories that contain a History file."""
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "History").is_file())


def _chrome_micros_to_iso(micros: int) -> str:
    return (_CHROME_EPOCH + timedelta(microseconds=micros)).isoformat()


def _domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).hostname or ""
    except (ValueError, AttributeError):
        return ""


def _read_visits(history_path: Path, since_micros: int) -> list[dict]:
    """Read visits > since. Uses immutable=1 to bypass Chrome's exclusive lock.

    immutable=1 tells SQLite the file won't change, so it skips locking and
    ignores the WAL. Trade-off: very recent visits still in Chrome's WAL won't
    appear until the next checkpoint, but they show up on the next sync.
    """
    rows: list[dict] = []
    con = sqlite3.connect(f"file:{history_path}?mode=ro&immutable=1", uri=True)
    try:
        cursor = con.execute(
            """
            SELECT v.id, u.url, u.title, v.visit_time, v.visit_duration, v.transition
            FROM visits v
            JOIN urls u ON v.url = u.id
            WHERE v.visit_time > ?
            ORDER BY v.visit_time ASC
            """,
            (since_micros,),
        )
        for vid, url, title, vtime, vdur, trans in cursor:
            rows.append(
                {
                    "visit_id": vid,
                    "url": url or "",
                    "title": title or "",
                    "visit_time": vtime or 0,
                    "visit_duration": vdur or 0,
                    "transition": trans or 0,
                }
            )
    finally:
        con.close()
    return rows


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    base = _profiles_root()
    profiles = _list_profiles(base)
    if not profiles:
        t.log.info("chrome_history: no Chrome profiles at %s", base)
        return

    cursor_str = t.cursor.get() or "0"
    try:
        cursor_micros = int(cursor_str)
    except ValueError:
        cursor_micros = 0

    all_rows: list[dict] = []
    new_cursor = cursor_micros

    for profile_dir in profiles:
        profile = profile_dir.name
        try:
            visits = _read_visits(profile_dir / "History", cursor_micros)
        except (sqlite3.Error, OSError) as e:
            t.log.warning("chrome_history: skipping %s: %s", profile, e)
            continue

        for v in visits:
            all_rows.append(
                {
                    "visit_id": v["visit_id"],
                    "profile": profile,
                    "url": v["url"],
                    "title": v["title"],
                    "domain": _domain_of(v["url"]),
                    "visited_at": _chrome_micros_to_iso(v["visit_time"]),
                    "duration_seconds": v["visit_duration"] / 1_000_000.0,
                    "transition": v["transition"],
                }
            )
            if v["visit_time"] > new_cursor:
                new_cursor = v["visit_time"]

    if all_rows:
        t.upsert("chrome_visits", all_rows, key=["visit_id", "profile"])
        t.cursor.set(str(new_cursor))
    t.log.info(
        "chrome_history: ingested %d visits across %d profiles",
        len(all_rows),
        len(profiles),
    )
