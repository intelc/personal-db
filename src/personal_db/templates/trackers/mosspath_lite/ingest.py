"""Mosspath Lite connector — local computer activity from Mosspath Lite SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_db.tracker import Tracker


def _source_db_path() -> Path:
    return Path(
        os.environ.get("MOSSPATH_LITE_DB")
        or "~/Library/Application Support/Mosspath Lite/mosspath-lite.sqlite"
    ).expanduser()


def _connect_source(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _iso(epoch_seconds: Any) -> str | None:
    if epoch_seconds is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch_seconds), UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> str:
    if value is None:
        values: list[Any] = []
    elif isinstance(value, list):
        values = value
    elif isinstance(value, str) and value:
        values = [value]
    else:
        values = []
    return json.dumps(values, separators=(",", ":"), sort_keys=True)


def _split_ids(value: Any) -> str:
    if isinstance(value, list):
        return _json_list(value)
    if not value:
        return "[]"
    return _json_list([part for part in str(value).split(",") if part])


def _import_events(t: Tracker, con: sqlite3.Connection) -> int:
    if not _table_exists(con, "action_events"):
        return 0

    rows: list[dict[str, Any]] = []
    for row in con.execute(
        """
        SELECT
          id, timestamp, action_type, app_name, bundle_id, window_title,
          browser_title, browser_url, browser_domain,
          focused_role, focused_title, focused_value_preview,
          clipboard_type, clipboard_preview,
          key_count, mouse_count, scroll_count,
          screenshot_path, context_key, note
        FROM action_events
        ORDER BY timestamp ASC
        """
    ):
        timestamp = _iso(row["timestamp"])
        if not row["id"] or not timestamp:
            continue
        rows.append(
            {
                "id": row["id"],
                "timestamp": timestamp,
                "action_type": row["action_type"] or "",
                "app_name": row["app_name"],
                "bundle_id": row["bundle_id"],
                "window_title": row["window_title"],
                "browser_title": row["browser_title"],
                "browser_url": row["browser_url"],
                "browser_domain": row["browser_domain"],
                "focused_role": row["focused_role"],
                "focused_title": row["focused_title"],
                "focused_preview": row["focused_value_preview"],
                "clipboard_type": row["clipboard_type"],
                "clipboard_preview": row["clipboard_preview"],
                "key_count": row["key_count"] or 0,
                "mouse_count": row["mouse_count"] or 0,
                "scroll_count": row["scroll_count"] or 0,
                "screenshot_path": row["screenshot_path"],
                "context_key": row["context_key"],
                "note": row["note"],
            }
        )

    return t.upsert("mosspath_lite_events", rows, key=["id"])


def _import_session_digests(t: Tracker, con: sqlite3.Connection) -> int:
    if not _table_exists(con, "session_digests"):
        return 0

    rows: list[dict[str, Any]] = []
    for row in con.execute(
        """
        SELECT session_id, started_at, ended_at, payload_json, confidence, generated_at
        FROM session_digests
        ORDER BY started_at ASC
        """
    ):
        payload = _payload(row["payload_json"])
        started_at = _iso(row["started_at"])
        ended_at = _iso(row["ended_at"])
        if not row["session_id"] or not started_at or not ended_at:
            continue
        rows.append(
            {
                "session_id": row["session_id"],
                "started_at": started_at,
                "ended_at": ended_at,
                "title": payload.get("title"),
                "what": payload.get("what"),
                "possible_intent": payload.get("possibleIntent"),
                "actions_json": _json_list(payload.get("actions")),
                "entities_json": _json_list(payload.get("entities")),
                "artifacts_json": _json_list(payload.get("artifacts")),
                "apps_json": _json_list(payload.get("apps")),
                "domains_json": _json_list(payload.get("domains")),
                "evidence_summary": payload.get("evidenceSummary"),
                "confidence": row["confidence"],
                "generated_at": _iso(row["generated_at"]),
            }
        )

    return t.upsert("mosspath_lite_session_digests", rows, key=["session_id"])


def _import_work_episodes(t: Tracker, con: sqlite3.Connection) -> int:
    if not _table_exists(con, "work_episodes"):
        return 0

    rows: list[dict[str, Any]] = []
    for row in con.execute(
        """
        SELECT
          id, started_at, ended_at, source_session_ids, boundary_score_ids,
          title, payload_json, confidence, status, generated_at
        FROM work_episodes
        ORDER BY started_at ASC
        """
    ):
        payload = _payload(row["payload_json"])
        started_at = _iso(row["started_at"])
        ended_at = _iso(row["ended_at"])
        if not row["id"] or not started_at or not ended_at:
            continue
        rows.append(
            {
                "id": row["id"],
                "started_at": started_at,
                "ended_at": ended_at,
                "title": row["title"] or payload.get("title"),
                "what": payload.get("what"),
                "why": payload.get("why"),
                "how_json": _json_list(payload.get("how")),
                "outcome": payload.get("outcome"),
                "source_session_ids_json": _split_ids(
                    payload.get("sourceSessionIDs") or row["source_session_ids"]
                ),
                "boundary_score_ids_json": _split_ids(
                    payload.get("boundaryScoreIDs") or row["boundary_score_ids"]
                ),
                "confidence": row["confidence"],
                "status": row["status"],
                "generated_at": _iso(row["generated_at"]),
            }
        )

    return t.upsert("mosspath_lite_work_episodes", rows, key=["id"])


def _import_routine_answers(t: Tracker, con: sqlite3.Connection) -> int:
    if not _table_exists(con, "routine_answers"):
        return 0

    rows: list[dict[str, Any]] = []
    for row in con.execute(
        """
        SELECT
          id, question_id, trigger_mode, started_at, ended_at,
          question_title, payload_json, confidence, generated_at
        FROM routine_answers
        ORDER BY ended_at ASC
        """
    ):
        payload = _payload(row["payload_json"])
        started_at = _iso(row["started_at"])
        ended_at = _iso(row["ended_at"])
        if not row["id"] or not started_at or not ended_at:
            continue
        rows.append(
            {
                "id": row["id"],
                "question_id": row["question_id"],
                "question_title": row["question_title"] or payload.get("questionTitle"),
                "trigger_mode": row["trigger_mode"],
                "started_at": started_at,
                "ended_at": ended_at,
                "answer_markdown": payload.get("answerMarkdown"),
                "evidence_ids_json": _json_list(payload.get("evidenceIDs")),
                "confidence": row["confidence"],
                "generated_at": _iso(row["generated_at"]),
            }
        )

    return t.upsert("mosspath_lite_routine_answers", rows, key=["id"])


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    path = _source_db_path()
    if not path.exists():
        t.log.info("mosspath_lite: source DB not found at %s", path)
        return

    con = _connect_source(path)
    try:
        counts = {
            "events": _import_events(t, con),
            "sessions": _import_session_digests(t, con),
            "episodes": _import_work_episodes(t, con),
            "answers": _import_routine_answers(t, con),
        }
    finally:
        con.close()

    t.cursor.set(datetime.now(UTC).isoformat())
    t.log.info("mosspath_lite: synced %s from %s", counts, path)
