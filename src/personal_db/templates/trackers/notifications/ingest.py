"""macOS notifications tracker.

Reads the local usernoted database. Content text is redacted by default; set
PERSONAL_DB_NOTIFICATIONS_STORE_CONTENT=1 to persist titles/subtitles/bodies.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import sqlite3
from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from personal_db.app_names import resolve_app_name
from personal_db.db import connect
from personal_db.tracker import Tracker

COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
DEFAULT_DB = Path("~/Library/Group Containers/group.com.apple.usernoted/db2/db").expanduser()
DEFAULT_CURSOR = "2020-01-01T00:00:00+00:00"
SOURCE = "usernoted"
ACTION_WINDOW = timedelta(minutes=10)
FAST_ACTION_WINDOW = timedelta(minutes=2)
GLANCE_WINDOW = timedelta(seconds=60)
BATCH_WINDOW = timedelta(minutes=2)
DERAIL_RETURN_WINDOW = timedelta(minutes=5)


def _source_db_path() -> Path:
    return Path(os.environ.get("PERSONAL_DB_NOTIFICATIONS_DB", str(DEFAULT_DB))).expanduser()


def _store_content() -> bool:
    return os.environ.get("PERSONAL_DB_NOTIFICATIONS_STORE_CONTENT", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _connect_source(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _iso_from_cocoa(seconds: Any) -> str | None:
    if seconds is None:
        return None
    try:
        return (COCOA_EPOCH + timedelta(seconds=float(seconds))).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _cocoa_from_iso(value: str) -> float:
    dt = _parse_iso(value)
    return (dt - COCOA_EPOCH).total_seconds()


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _content_hash(bundle_id: str | None, title: str, subtitle: str, body: str) -> str:
    payload = "\u001f".join([bundle_id or "", title, subtitle, body])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                continue
        text = _text(value)
        if text:
            return text
    return None


def _parse_payload(blob: bytes) -> dict[str, str]:
    try:
        plist = plistlib.loads(blob)
    except Exception:
        return {}
    if not isinstance(plist, dict):
        return {}
    req = plist.get("req")
    if not isinstance(req, dict):
        req = plist

    title = _first_text(req, ("titl", "title")) or ""
    subtitle = _first_text(req, ("subt", "subtitle")) or ""
    body = _first_text(req, ("body", "message")) or ""
    return {
        "title": title,
        "subtitle": subtitle,
        "body": body,
        "thread_id": _first_text(req, ("threadIdentifier", "thread-id", "thread_id", "tid")) or "",
        "category_id": _first_text(req, ("categoryIdentifier", "category-id", "category_id", "cat")) or "",
        "request_id": _first_text(req, ("identifier", "requestIdentifier", "req-id", "uuid")) or "",
    }


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _source_rows(con: sqlite3.Connection, cursor_cocoa: float) -> list[sqlite3.Row]:
    if not _table_exists(con, "record") or not _table_exists(con, "app"):
        raise RuntimeError("usernoted database is missing expected record/app tables")
    return list(
        con.execute(
            """
            SELECT
              record.rowid AS source_record_id,
              app.identifier AS bundle_id,
              record.data AS data,
              record.delivered_date AS delivered_date
            FROM record
            LEFT JOIN app ON app.app_id = record.app_id
            WHERE record.delivered_date > ?
            ORDER BY record.delivered_date ASC, record.rowid ASC
            """,
            (cursor_cocoa,),
        )
    )


def _app_name(bundle_id: str | None, cache: dict[str, str]) -> str | None:
    if not bundle_id:
        return None
    if bundle_id not in cache:
        cache[bundle_id] = resolve_app_name(bundle_id) or bundle_id
    return cache[bundle_id]


def _notification_rows(source_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    keep_content = _store_content()
    imported_at = datetime.now(UTC).isoformat()
    app_cache: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for source in source_rows:
        delivered_at = _iso_from_cocoa(source["delivered_date"])
        if not delivered_at:
            continue
        raw_data = source["data"]
        if raw_data is None:
            continue
        payload = _parse_payload(bytes(raw_data))
        title = payload.get("title", "")
        subtitle = payload.get("subtitle", "")
        body = payload.get("body", "")
        if not (title or subtitle or body):
            continue
        bundle_id = source["bundle_id"]
        rows.append(
            {
                "source_record_id": str(source["source_record_id"]),
                "delivered_at": delivered_at,
                "bundle_id": bundle_id,
                "app_name": _app_name(bundle_id, app_cache),
                "title": title if keep_content else None,
                "subtitle": subtitle if keep_content else None,
                "body": body if keep_content else None,
                "title_hash": _hash(title),
                "subtitle_hash": _hash(subtitle),
                "body_hash": _hash(body),
                "content_hash": _content_hash(bundle_id, title, subtitle, body),
                "thread_id": payload.get("thread_id") or None,
                "category_id": payload.get("category_id") or None,
                "request_id": payload.get("request_id") or None,
                "source": SOURCE,
                "imported_at": imported_at,
            }
        )
    return rows


def sync(t: Tracker) -> None:
    db = _source_db_path()
    if not db.exists():
        raise RuntimeError(f"usernoted notification DB not found at {db}")
    cursor_iso = t.cursor.get(default=DEFAULT_CURSOR) or DEFAULT_CURSOR
    try:
        cursor_cocoa = _cocoa_from_iso(cursor_iso)
    except ValueError:
        cursor_cocoa = _cocoa_from_iso(DEFAULT_CURSOR)

    try:
        con = _connect_source(db)
        try:
            raw_rows = _source_rows(con, cursor_cocoa)
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"unable to read usernoted notification DB at {db}; grant Full Disk Access "
            "to the personal_db daemon/Python interpreter"
        ) from exc

    rows = _notification_rows(raw_rows)
    if rows:
        t.upsert("notifications_events", rows, key=["source_record_id"])
        t.cursor.set(rows[-1]["delivered_at"])
    _materialize_impacts(t)
    t.log.info("notifications: ingested %d rows", len(rows))


def backfill(t: Tracker, start, end) -> None:
    if start:
        t.cursor.set(_parse_iso(str(start)).isoformat())
    else:
        t.cursor.set(DEFAULT_CURSOR)
    sync(t)


def _same_source(notification: dict[str, Any], event: dict[str, Any] | None) -> bool:
    if not event:
        return False
    nb = _text(notification.get("bundle_id")).lower()
    eb = _text(event.get("bundle_id")).lower()
    if nb and eb and nb == eb:
        return True
    na = _text(notification.get("app_name")).lower()
    ea = _text(event.get("app_name")).lower()
    return bool(na and ea and (na == ea or na in ea or ea in na))


def _same_context(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    lb = _text(left.get("bundle_id")).lower()
    rb = _text(right.get("bundle_id")).lower()
    if lb and rb:
        return lb == rb
    la = _text(left.get("app_name")).lower()
    ra = _text(right.get("app_name")).lower()
    return bool(la and ra and la == ra)


def _read_events(con: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(con, "mosspath_lite_events"):
        return []
    rows = con.execute(
        """
        SELECT id, timestamp, app_name, bundle_id
        FROM mosspath_lite_events
        WHERE timestamp IS NOT NULL
        ORDER BY timestamp ASC
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            ts = _parse_iso(row["timestamp"])
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "dt": ts,
                "app_name": row["app_name"],
                "bundle_id": row["bundle_id"],
            }
        )
    return out


def _batch_counts(notifications: list[dict[str, Any]]) -> dict[str, int]:
    by_source: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for row in notifications:
        source = _text(row.get("bundle_id")) or _text(row.get("app_name")) or "(unknown)"
        by_source[source].append((row["dt"], row["source_record_id"]))

    counts: dict[str, int] = {}
    for items in by_source.values():
        times = [dt for dt, _ in items]
        for dt, row_id in items:
            lo = bisect_left(times, dt - BATCH_WINDOW)
            hi = bisect_left(times, dt + BATCH_WINDOW + timedelta(microseconds=1))
            counts[row_id] = hi - lo
    return counts


def _impact_for(
    notification: dict[str, Any],
    events: list[dict[str, Any]],
    event_times: list[datetime],
    batch_count: int,
    computed_at: str,
) -> dict[str, Any]:
    delivered = notification["dt"]
    lo = bisect_left(event_times, delivered)
    prior = events[lo - 1] if lo > 0 else None
    window_end = delivered + ACTION_WINDOW
    after = events[lo : bisect_left(event_times, window_end + timedelta(microseconds=1))]
    next_event = after[0] if after else None

    source_event = next((event for event in after if _same_source(notification, event)), None)
    returned = None
    away_seconds = None
    impact = "ignored"
    confidence = 0.65
    evidence = "no nearby foreground activity"
    acted_at = None
    seconds_to_action = None

    if source_event is not None:
        seconds_to_action = max(0, int((source_event["dt"] - delivered).total_seconds()))
        acted_at = source_event["timestamp"]
        returned = next(
            (
                event
                for event in after
                if event["dt"] > source_event["dt"]
                and _same_context(prior, event)
                and event["dt"] <= source_event["dt"] + DERAIL_RETURN_WINDOW
            ),
            None,
        )
        if prior and not _same_context(prior, source_event) and returned is None:
            impact = "derailed"
            confidence = 0.76 if seconds_to_action <= FAST_ACTION_WINDOW.total_seconds() else 0.62
            away_seconds = int((min(window_end, source_event["dt"] + DERAIL_RETURN_WINDOW) - source_event["dt"]).total_seconds())
            evidence = "switched from prior context to notification source without a quick return"
        else:
            impact = "acted_on"
            confidence = 0.82 if seconds_to_action <= FAST_ACTION_WINDOW.total_seconds() else 0.68
            if returned:
                away_seconds = int((returned["dt"] - source_event["dt"]).total_seconds())
            evidence = "foreground activity matched the notification source"
    elif next_event is not None:
        seconds_to_next = int((next_event["dt"] - delivered).total_seconds())
        if seconds_to_next <= GLANCE_WINDOW.total_seconds():
            impact = "glanced"
            confidence = 0.56
            evidence = "activity resumed soon after delivery but not in the source app"
        elif batch_count >= 3:
            impact = "batched"
            confidence = 0.58
            evidence = "same-source notification burst with no source-app action"
        else:
            impact = "ignored"
            confidence = 0.6
            evidence = "no source-app action inside the analysis window"
    elif batch_count >= 3:
        impact = "batched"
        confidence = 0.58
        evidence = "same-source notification burst with no nearby foreground activity"

    return {
        "notification_id": notification["source_record_id"],
        "delivered_at": notification["delivered_at"],
        "bundle_id": notification.get("bundle_id"),
        "app_name": notification.get("app_name"),
        "impact": impact,
        "confidence": confidence,
        "evidence": evidence,
        "batch_count": batch_count,
        "prior_event_id": prior.get("id") if prior else None,
        "prior_at": prior.get("timestamp") if prior else None,
        "prior_app_name": prior.get("app_name") if prior else None,
        "prior_bundle_id": prior.get("bundle_id") if prior else None,
        "next_event_id": next_event.get("id") if next_event else None,
        "next_at": next_event.get("timestamp") if next_event else None,
        "next_app_name": next_event.get("app_name") if next_event else None,
        "next_bundle_id": next_event.get("bundle_id") if next_event else None,
        "acted_at": acted_at,
        "seconds_to_action": seconds_to_action,
        "returned_at": returned.get("timestamp") if returned else None,
        "away_seconds": away_seconds,
        "computed_at": computed_at,
    }


def _materialize_impacts(t: Tracker) -> None:
    con = connect(t.cfg.db_path)
    con.row_factory = sqlite3.Row
    try:
        notifications = []
        for row in con.execute(
            """
            SELECT source_record_id, delivered_at, bundle_id, app_name
            FROM notifications_events
            ORDER BY delivered_at ASC, source_record_id ASC
            """
        ):
            try:
                dt = _parse_iso(row["delivered_at"])
            except (TypeError, ValueError):
                continue
            notifications.append({**dict(row), "dt": dt})
        if not notifications:
            return

        events = _read_events(con)
        event_times = [event["dt"] for event in events]
        counts = _batch_counts(notifications)
        computed_at = datetime.now(UTC).isoformat()
        impacts = [
            _impact_for(row, events, event_times, counts.get(row["source_record_id"], 1), computed_at)
            for row in notifications
        ]
        con.executemany("DELETE FROM notification_impacts WHERE notification_id=?", [(r["notification_id"],) for r in impacts])
        con.commit()
    finally:
        con.close()
    t.upsert("notification_impacts", impacts, key=["notification_id"])
