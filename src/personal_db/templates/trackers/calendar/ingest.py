"""macOS Calendar tracker with planned-vs-actual block materialization."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from personal_db.db import connect
from personal_db.tracker import Tracker

COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
SOURCE = "macos_calendar"
DEFAULT_ROOT = Path("~/Library/Group Containers/group.com.apple.calendar").expanduser()
WINDOW_PAST_DAYS = 120
WINDOW_FUTURE_DAYS = 45


def _env_db() -> Path | None:
    raw = os.environ.get("PERSONAL_DB_CALENDAR_DB")
    return Path(raw).expanduser() if raw else None


def _source_candidates() -> list[Path]:
    explicit = _env_db()
    if explicit:
        return [explicit]
    names = {
        "calendar cache",
        "calendar.sqlitedb",
        "calendar cache.sqlite",
        "calendar.sqlite",
    }
    roots = [
        DEFAULT_ROOT,
        Path("~/Library/Containers/com.apple.iCal/Data/Library/Calendars").expanduser(),
        Path("~/Library/Calendars").expanduser(),
    ]
    out: list[Path] = []
    for root in roots:
        try:
            if root.is_file():
                out.append(root)
                continue
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                lower = path.name.lower()
                if lower in names or lower.endswith((".sqlite", ".sqlitedb", ".db")):
                    out.append(path)
        except (OSError, PermissionError):
            continue
    return sorted(dict.fromkeys(out))


def _connect_source(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if row[0]
    }


def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}


def _pick(cols: set[str], *names: str) -> str | None:
    for name in names:
        if name in cols:
            return name
    upper = {c.upper(): c for c in cols}
    for name in names:
        found = upper.get(name.upper())
        if found:
            return found
    return None


def _is_probable_calendar_store(con: sqlite3.Connection) -> bool:
    tables = _tables(con)
    if "ZCALENDARITEM" in tables:
        cols = _cols(con, "ZCALENDARITEM")
        return bool(_pick(cols, "ZSTARTDATE") and _pick(cols, "ZENDDATE"))
    for table in tables:
        cols = _cols(con, table)
        has_time = _pick(cols, "start_at", "start", "start_date", "dtstart")
        has_end = _pick(cols, "end_at", "end", "end_date", "dtend")
        has_title = _pick(cols, "title", "summary", "name")
        if has_time and has_end and has_title:
            return True
    return False


def _num_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    # Calendar CoreData dates are Cocoa epoch seconds. Some generic sources may
    # use Unix seconds or milliseconds; detect by rough magnitude.
    if n > 10_000_000_000:
        return datetime.fromtimestamp(n / 1000.0, UTC).isoformat()
    if n > 1_000_000_000:
        return datetime.fromtimestamp(n, UTC).isoformat()
    return (COCOA_EPOCH + timedelta(seconds=n)).isoformat()


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _num_to_iso(value)
    text = str(value).strip()
    if not text:
        return None
    if text.replace(".", "", 1).lstrip("-").isdigit():
        return _num_to_iso(text)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _hash(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(row: sqlite3.Row, col: str | None) -> str | None:
    if not col:
        return None
    value = row[col]
    text = str(value or "").strip()
    return text or None


def _int(row: sqlite3.Row, col: str | None, default: int = 0) -> int:
    if not col:
        return default
    try:
        return int(row[col] or 0)
    except (TypeError, ValueError):
        return default


def _event_id(path: Path, source_pk: str | None, uid: str | None, start_at: str) -> str:
    stable = "|".join([str(path), source_pk or "", uid or "", start_at])
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _durable_event_id(path: Path, uid: str) -> str:
    # Keyed on the store's per-row UID alone so renames/reschedules update the
    # same event instead of forking a ghost copy. Rows without a durable UID
    # keep the legacy composite identity above.
    return hashlib.sha256(f"uid|{path}|{uid}".encode("utf-8")).hexdigest()


def _import_coredata_events(path: Path, con: sqlite3.Connection) -> list[dict[str, Any]]:
    cols = _cols(con, "ZCALENDARITEM")
    start_col = _pick(cols, "ZSTARTDATE")
    end_col = _pick(cols, "ZENDDATE")
    title_col = _pick(cols, "ZTITLE", "ZSUMMARY")
    pk_col = _pick(cols, "Z_PK")
    uid_col = _pick(cols, "ZUUID", "ZUNIQUEID", "ZEXTERNALID")
    # Durable identity only from the per-row ZUUID; ZUNIQUEID/ZEXTERNALID can
    # be shared across recurrence rows (see _import_generic_events).
    durable_col = _pick(cols, "ZUUID")
    cal_fk_col = _pick(cols, "ZCALENDAR")
    location_col = _pick(cols, "ZLOCATION")
    notes_col = _pick(cols, "ZNOTES", "ZDESCRIPTION")
    all_day_col = _pick(cols, "ZALLDAY")
    tz_col = _pick(cols, "ZTIMEZONE", "ZTIMEZONEID")
    url_col = _pick(cols, "ZURL")
    status_col = _pick(cols, "ZSTATUS")
    availability_col = _pick(cols, "ZAVAILABILITY")
    if not start_col or not end_col:
        return []

    cal_titles: dict[str, str] = {}
    if "ZCALENDAR" in _tables(con):
        cal_cols = _cols(con, "ZCALENDAR")
        cal_pk = _pick(cal_cols, "Z_PK")
        cal_title = _pick(cal_cols, "ZTITLE", "ZNAME")
        if cal_pk and cal_title:
            for row in con.execute(f'SELECT "{cal_pk}", "{cal_title}" FROM ZCALENDAR'):
                cal_titles[str(row[0])] = str(row[1] or "")

    select_cols = [c for c in {
        pk_col,
        uid_col,
        durable_col,
        cal_fk_col,
        title_col,
        location_col,
        notes_col,
        start_col,
        end_col,
        all_day_col,
        tz_col,
        url_col,
        status_col,
        availability_col,
    } if c]
    select_sql = ", ".join(f'"{c}"' for c in select_cols)
    rows = con.execute(f"SELECT {select_sql} FROM ZCALENDARITEM").fetchall()
    imported_at = datetime.now(UTC).isoformat()
    out: list[dict[str, Any]] = []
    for row in rows:
        start_at = _to_iso(row[start_col])
        end_at = _to_iso(row[end_col])
        if not start_at or not end_at:
            continue
        source_pk = _text(row, pk_col)
        uid = _text(row, uid_col)
        durable = _text(row, durable_col)
        calendar_id = _text(row, cal_fk_col)
        out.append(
            {
                "event_id": _durable_event_id(path, durable)
                if durable
                else _event_id(path, source_pk, uid, start_at),
                "source": SOURCE,
                "source_db": str(path),
                "source_pk": source_pk,
                "calendar_id": calendar_id,
                "calendar_title": cal_titles.get(calendar_id or ""),
                "title": _text(row, title_col) or "(untitled)",
                "location": _text(row, location_col),
                "notes_hash": _hash(row[notes_col]) if notes_col else None,
                "start_at": start_at,
                "end_at": end_at,
                "all_day": _int(row, all_day_col),
                "timezone": _text(row, tz_col),
                "url": _text(row, url_col),
                "status": _text(row, status_col),
                "availability": _text(row, availability_col),
                "deleted_at": None,
                "imported_at": imported_at,
            }
        )
    return out


def _generic_calendar_titles(con: sqlite3.Connection, tables: set[str]) -> dict[str, str]:
    # Calendar.sqlitedb keeps the calendar name in a separate Calendar table
    # keyed by ROWID; events only carry the integer calendar_id FK.
    for cal_table in ("Calendar", "Calendars", "calendars"):
        if cal_table not in tables:
            continue
        cal_cols = _cols(con, cal_table)
        title_col = _pick(cal_cols, "title", "name")
        if not title_col:
            return {}
        return {
            str(row[0]): str(row[1] or "")
            for row in con.execute(f'SELECT ROWID, "{title_col}" FROM "{cal_table}"')
        }
    return {}


def _import_generic_events(path: Path, con: sqlite3.Connection) -> list[dict[str, Any]]:
    imported_at = datetime.now(UTC).isoformat()
    out: list[dict[str, Any]] = []
    tables = _tables(con)
    cal_titles = _generic_calendar_titles(con, tables)
    for table in sorted(tables):
        # Change-log/history siblings (e.g. CalendarItemChanges) carry copies
        # of edited events, not events — importing them forks phantom rows.
        if table.lower().endswith(("changes", "history")):
            continue
        cols = _cols(con, table)
        start_col = _pick(cols, "start_at", "start", "start_date", "dtstart")
        end_col = _pick(cols, "end_at", "end", "end_date", "dtend")
        title_col = _pick(cols, "title", "summary", "name")
        if not start_col or not end_col or not title_col:
            continue
        pk_col = _pick(cols, "id", "uid", "uuid", "event_id")
        # Durable per-row UID only: uuid/uid. NOT unique_identifier — that is
        # the iCal UID, shared between a recurring master and its detached
        # occurrences, so keying on it would collapse them.
        uid_col = _pick(cols, "uuid") or _pick(cols, "uid")
        cal_col = _pick(cols, "calendar", "calendar_name", "calendar_title")
        cal_fk_col = _pick(cols, "calendar_id")
        location_col = _pick(cols, "location")
        all_day_col = _pick(cols, "all_day", "allday")
        rows = con.execute(f'SELECT * FROM "{table}"').fetchall()
        for row in rows:
            start_at = _to_iso(row[start_col])
            end_at = _to_iso(row[end_col])
            if not start_at or not end_at:
                continue
            source_pk = _text(row, pk_col)
            uid = _text(row, uid_col)
            calendar_id = _text(row, cal_fk_col)
            calendar_title = _text(row, cal_col) or (
                cal_titles.get(calendar_id) if calendar_id else None
            )
            out.append(
                {
                    "event_id": _durable_event_id(path, uid)
                    if uid
                    else _event_id(path, source_pk, _text(row, title_col), start_at),
                    "source": SOURCE,
                    "source_db": str(path),
                    "source_pk": source_pk,
                    "calendar_id": calendar_id,
                    "calendar_title": calendar_title,
                    "title": _text(row, title_col) or "(untitled)",
                    "location": _text(row, location_col),
                    "notes_hash": None,
                    "start_at": start_at,
                    "end_at": end_at,
                    "all_day": _int(row, all_day_col),
                    "timezone": None,
                    "url": None,
                    "status": None,
                    "availability": None,
                    "deleted_at": None,
                    "imported_at": imported_at,
                }
            )
    return out


def _read_calendar_db(path: Path) -> list[dict[str, Any]]:
    con = _connect_source(path)
    try:
        if not _is_probable_calendar_store(con):
            return []
        if "ZCALENDARITEM" in _tables(con):
            return _import_coredata_events(path, con)
        return _import_generic_events(path, con)
    finally:
        con.close()


def _ensure_deleted_at_column(t: Tracker) -> None:
    con = connect(t.cfg.db_path)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(calendar_events)")}
        if cols and "deleted_at" not in cols:
            con.execute("ALTER TABLE calendar_events ADD COLUMN deleted_at TEXT")
            con.commit()
    finally:
        con.close()


def _tombstone_missing(t: Tracker, read_paths: list[str], seen_ids: set[str]) -> None:
    """Full-snapshot diff: a live row whose store was read this run but no
    longer contains it was deleted (or re-keyed) upstream. Soft-delete it and
    bump imported_at so watermark consumers re-stage the tombstone. Stores
    that failed to read are left untouched, and reappearing events resurrect
    via the upsert writing deleted_at=NULL."""
    if not read_paths:
        return
    now = datetime.now(UTC).isoformat()
    con = connect(t.cfg.db_path)
    try:
        placeholders = ",".join("?" * len(read_paths))
        live = con.execute(
            f"SELECT event_id FROM calendar_events "
            f"WHERE deleted_at IS NULL AND source_db IN ({placeholders})",
            read_paths,
        ).fetchall()
        gone = [row[0] for row in live if row[0] not in seen_ids]
        if gone:
            con.executemany(
                "UPDATE calendar_events SET deleted_at=?, imported_at=? WHERE event_id=?",
                [(now, now, event_id) for event_id in gone],
            )
            con.commit()
        if gone:
            t.log.info("calendar: tombstoned %d events removed upstream", len(gone))
    finally:
        con.close()


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return (
        con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        is not None
    )


def _overlap_seconds(start: datetime, end: datetime, row_start: str, row_end: str) -> float:
    try:
        s = _parse_iso(row_start)
        e = _parse_iso(row_end)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (min(end, e) - max(start, s)).total_seconds())


def _json_counts(counter: Counter[str], *, limit: int = 8) -> str:
    return json.dumps(
        [{"label": label, "count": count} for label, count in counter.most_common(limit)],
        separators=(",", ":"),
    )


def _project_rows(con: sqlite3.Connection, local_date: str) -> str:
    if not _table_exists(con, "project_time"):
        return "[]"
    rows = con.execute(
        """
        SELECT project, hours, commit_count, breakdown_json
        FROM project_time
        WHERE date = ?
        ORDER BY hours DESC, commit_count DESC
        LIMIT 8
        """,
        (local_date,),
    ).fetchall()
    return json.dumps([dict(row) for row in rows], separators=(",", ":"))


def _label(planned_min: int, actual_min: int, app_count: int, domain_count: int, all_day: int) -> str:
    if all_day:
        return "calendar_only"
    if actual_min <= 0:
        return "no_activity"
    if actual_min < min(10, planned_min * 0.25):
        return "light_activity"
    if app_count <= 2 and domain_count <= 2:
        return "focused"
    return "fragmented"


def _fragmentation(app_count: int, domain_count: int, planned_min: int) -> float:
    if planned_min <= 0:
        return 0.0
    raw = (max(0, app_count - 1) * 0.16) + (max(0, domain_count - 1) * 0.10)
    return round(min(1.0, raw), 3)


def _materialize_reality(t: Tracker) -> None:
    con = connect(t.cfg.db_path)
    con.row_factory = sqlite3.Row
    try:
        if _table_exists(con, "calendar_reality_blocks"):
            con.execute(
                """
                DELETE FROM calendar_reality_blocks WHERE event_id IN
                  (SELECT event_id FROM calendar_events WHERE deleted_at IS NOT NULL)
                """
            )
            con.commit()
        events = list(
            con.execute(
                """
                SELECT event_id, title, calendar_title, start_at, end_at, all_day
                FROM calendar_events
                WHERE start_at >= ? AND start_at <= ?
                  AND end_at > start_at AND deleted_at IS NULL
                ORDER BY start_at
                """,
                (
                    (datetime.now(UTC) - timedelta(days=WINDOW_PAST_DAYS)).isoformat(),
                    (datetime.now(UTC) + timedelta(days=WINDOW_FUTURE_DAYS)).isoformat(),
                ),
            )
        )
        computed_at = datetime.now(UTC).isoformat()
        rows: list[dict[str, Any]] = []
        has_screen = _table_exists(con, "screen_time_app_usage")
        has_names = _table_exists(con, "screen_time_app_names")
        has_mp = _table_exists(con, "mosspath_lite_events")
        has_chrome = _table_exists(con, "chrome_visits")

        for event in events:
            start = _parse_iso(event["start_at"])
            end = _parse_iso(event["end_at"])
            planned_min = max(0, int((end - start).total_seconds() / 60))
            local_date = start.astimezone().strftime("%Y-%m-%d")
            app_seconds: Counter[str] = Counter()
            domain_counts: Counter[str] = Counter()
            screen_seconds = 0.0
            mosspath_events = 0
            chrome_visits = 0

            if int(event["all_day"] or 0):
                rows.append(
                    {
                        "event_id": event["event_id"],
                        "date": local_date,
                        "title": event["title"],
                        "calendar_title": event["calendar_title"],
                        "start_at": event["start_at"],
                        "end_at": event["end_at"],
                        "planned_minutes": planned_min,
                        "actual_minutes": 0,
                        "screen_time_minutes": 0,
                        "mosspath_events": 0,
                        "chrome_visits": 0,
                        "app_count": 0,
                        "domain_count": 0,
                        "top_apps_json": "[]",
                        "top_domains_json": "[]",
                        "projects_json": "[]",
                        "reality_label": "calendar_only",
                        "fragmentation_score": 0.0,
                        "computed_at": computed_at,
                    }
                )
                continue

            if has_screen:
                join_names = (
                    "LEFT JOIN screen_time_app_names n ON n.bundle_id = s.bundle_id"
                    if has_names
                    else ""
                )
                name_expr = "coalesce(n.app_name, s.bundle_id)" if has_names else "s.bundle_id"
                for row in con.execute(
                    f"""
                    SELECT {name_expr} AS app_name, s.start_at, s.end_at
                    FROM screen_time_app_usage s
                    {join_names}
                    WHERE s.start_at < ? AND s.end_at > ?
                    """,
                    (event["end_at"], event["start_at"]),
                ):
                    overlap = _overlap_seconds(start, end, row["start_at"], row["end_at"])
                    if overlap > 0:
                        screen_seconds += overlap
                        app_seconds[row["app_name"] or "(unknown)"] += int(overlap)

            if has_mp:
                for row in con.execute(
                    """
                    SELECT app_name, bundle_id, browser_domain
                    FROM mosspath_lite_events
                    WHERE timestamp >= ? AND timestamp < ?
                    """,
                    (event["start_at"], event["end_at"]),
                ):
                    mosspath_events += 1
                    app_seconds[row["app_name"] or row["bundle_id"] or "(unknown)"] += 1
                    if row["browser_domain"]:
                        domain_counts[row["browser_domain"]] += 1

            if has_chrome:
                for row in con.execute(
                    """
                    SELECT domain
                    FROM chrome_visits
                    WHERE visited_at >= ? AND visited_at < ?
                    """,
                    (event["start_at"], event["end_at"]),
                ):
                    chrome_visits += 1
                    if row["domain"]:
                        domain_counts[row["domain"]] += 1

            actual_min = max(int(screen_seconds / 60), min(planned_min, mosspath_events))
            app_count = len(app_seconds)
            domain_count = len(domain_counts)
            label = _label(planned_min, actual_min, app_count, domain_count, int(event["all_day"] or 0))
            rows.append(
                {
                    "event_id": event["event_id"],
                    "date": local_date,
                    "title": event["title"],
                    "calendar_title": event["calendar_title"],
                    "start_at": event["start_at"],
                    "end_at": event["end_at"],
                    "planned_minutes": planned_min,
                    "actual_minutes": actual_min,
                    "screen_time_minutes": int(screen_seconds / 60),
                    "mosspath_events": mosspath_events,
                    "chrome_visits": chrome_visits,
                    "app_count": app_count,
                    "domain_count": domain_count,
                    "top_apps_json": json.dumps(
                        [
                            {"label": k, "minutes": round(v / 60, 1) if v > 60 else v}
                            for k, v in app_seconds.most_common(8)
                        ],
                        separators=(",", ":"),
                    ),
                    "top_domains_json": _json_counts(domain_counts),
                    "projects_json": _project_rows(con, local_date),
                    "reality_label": label,
                    "fragmentation_score": _fragmentation(app_count, domain_count, planned_min),
                    "computed_at": computed_at,
                }
            )
        if rows:
            con.executemany("DELETE FROM calendar_reality_blocks WHERE event_id=?", [(r["event_id"],) for r in rows])
            con.commit()
    finally:
        con.close()
    if rows:
        t.upsert("calendar_reality_blocks", rows, key=["event_id"])


def sync(t: Tracker) -> None:
    rows: list[dict[str, Any]] = []
    candidates = _source_candidates()
    errors: list[str] = []
    read_paths: list[str] = []
    for path in candidates:
        try:
            rows.extend(_read_calendar_db(path))
            read_paths.append(str(path))
        except (sqlite3.Error, OSError, PermissionError) as exc:
            errors.append(f"{path}: {exc}")
            continue
    if not candidates:
        raise RuntimeError(
            "no Calendar SQLite stores found; set PERSONAL_DB_CALENDAR_DB or grant Full Disk Access"
        )
    if not rows and errors:
        raise RuntimeError("could not read Calendar stores: " + "; ".join(errors[:3]))
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["event_id"], row)
    if len(unique) < len(rows):
        t.log.warning("calendar: dropped %d rows with duplicate event_id", len(rows) - len(unique))
    rows = list(unique.values())
    _ensure_deleted_at_column(t)
    if rows:
        t.upsert("calendar_events", rows, key=["event_id"])
        latest = max(row["start_at"] for row in rows)
        t.cursor.set(latest)
    _tombstone_missing(t, read_paths, {row["event_id"] for row in rows})
    _materialize_reality(t)
    t.log.info("calendar: ingested %d events from %d candidate stores", len(rows), len(candidates))


def backfill(t: Tracker, start, end) -> None:
    sync(t)
