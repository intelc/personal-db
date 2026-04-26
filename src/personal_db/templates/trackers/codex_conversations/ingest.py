from __future__ import annotations

import ast
import json
import logging
import os
from pathlib import Path

from personal_db.tracker import Tracker

log = logging.getLogger(__name__)


def _codex_root() -> Path:
    return Path(os.environ.get("CODEX_SESSIONS_DIR") or "~/.codex/sessions").expanduser()


def _parse_payload(raw) -> dict | None:
    """Parse a payload that may be a dict already, a JSON string, or a Python repr string."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    # Try JSON first
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    # Fall back to Python literal eval (handles dict reprs from some tools)
    try:
        result = ast.literal_eval(raw)
        if isinstance(result, dict):
            return result
    except (ValueError, SyntaxError):
        pass
    return None


def _extract_text(content) -> str:
    """Extract text from a content field that may be a string or list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def _filename_uuid(path: Path) -> str | None:
    """Extract UUID from filename like rollout-<iso-ts>-<uuid>.jsonl.
    The UUID is the last hyphen-separated group of the stem."""
    stem = path.stem  # e.g. rollout-2026-04-26T10:00:00-abc123uuid
    # UUID is everything after the last occurrence of a known separator pattern.
    # Split on '-' and take last 5 segments (standard UUID format: 8-4-4-4-12)
    # but simpler: take the last 36 chars if they look like a UUID.
    parts = stem.split("-")
    # A UUID has 5 hyphen-separated groups (8-4-4-4-12 = 36 chars total with dashes)
    # Try to reconstruct from the tail
    for start in range(len(parts) - 1, -1, -1):
        candidate = "-".join(parts[start:])
        if len(candidate) == 36 and candidate.count("-") == 4:
            return candidate
    # Fallback: use the whole stem
    return stem


def _parse_session(jsonl_path: Path) -> dict | None:
    """Parse a Codex JSONL session file into a session row dict."""
    session_id = None
    started_at = None
    last_event_at = None
    cwd = None
    event_count = 0
    user_msg_count = 0
    assistant_msg_count = 0
    first_user_prompt = None

    fallback_uuid = _filename_uuid(jsonl_path)

    try:
        with jsonl_path.open(encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    line = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                line_type = line.get("type", "")

                # Track the latest timestamp from any line
                ts = line.get("timestamp")
                if ts and (last_event_at is None or ts > last_event_at):
                    last_event_at = ts

                if line_type == "session_meta":
                    payload = _parse_payload(line.get("payload"))
                    if payload is not None:
                        session_id = payload.get("id") or fallback_uuid
                        started_at = payload.get("timestamp")

                elif line_type == "turn_context":
                    if cwd is None:
                        payload = _parse_payload(line.get("payload"))
                        if payload is not None:
                            cwd = payload.get("cwd")

                elif line_type == "response_item":
                    payload = _parse_payload(line.get("payload"))
                    if payload is None:
                        continue
                    role = payload.get("role", "")
                    if role in {"user", "assistant"}:
                        event_count += 1
                        if role == "user":
                            user_msg_count += 1
                            if first_user_prompt is None:
                                content = payload.get("content", "")
                                text = _extract_text(content)
                                first_user_prompt = text[:500] if text else None
                        else:
                            assistant_msg_count += 1

    except OSError as exc:
        log.warning("codex_conversations: could not read %s: %s", jsonl_path, exc)
        return None

    if started_at is None:
        return None

    return {
        "session_id": session_id or fallback_uuid,
        "cwd": cwd,
        "started_at": started_at,
        "last_event_at": last_event_at or started_at,
        "event_count": event_count,
        "user_msg_count": user_msg_count,
        "assistant_msg_count": assistant_msg_count,
        "first_user_prompt": first_user_prompt,
    }


def backfill(t: Tracker, start, end) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    sessions_root = _codex_root()
    if not sessions_root.exists():
        log.info("codex_conversations: %s not found, skipping", sessions_root)
        return

    cursor_raw = t.cursor.get(default="0")
    try:
        cursor_mtime = float(cursor_raw)
    except (TypeError, ValueError):
        cursor_mtime = 0.0

    max_mtime = cursor_mtime
    rows = []

    for jsonl_file in sorted(sessions_root.rglob("*.jsonl")):
        try:
            file_mtime = jsonl_file.stat().st_mtime
        except OSError:
            continue
        if file_mtime <= cursor_mtime:
            continue
        if file_mtime > max_mtime:
            max_mtime = file_mtime

        session = _parse_session(jsonl_file)
        if session is not None:
            rows.append(session)

    if rows:
        t.upsert("codex_sessions", rows, key=["session_id"])

    t.cursor.set(str(max_mtime))
    log.info("codex_conversations: ingested %d session(s)", len(rows))
