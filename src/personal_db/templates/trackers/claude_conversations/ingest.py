from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from personal_db.tracker import Tracker

log = logging.getLogger(__name__)

_SKIP_TYPES = {
    "permission-mode",
    "attachment",
    "file-history-snapshot",
    "system",
    "last-prompt",
    "queue-operation",
}


def _claude_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECTS_DIR") or "~/.claude/projects").expanduser()


def _extract_text(content) -> str:
    """Extract text from a message content field that is either a string or list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def _parse_session(jsonl_path: Path) -> dict | None:
    """Stream a JSONL session file and return a session row dict, or None if empty."""
    started_at = None
    last_msg_at = None
    message_count = 0
    user_msg_count = 0
    assistant_msg_count = 0
    first_user_prompt = None
    session_id = jsonl_path.stem  # UUID from filename

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

                msg_type = line.get("type", "")
                if msg_type in _SKIP_TYPES:
                    continue

                if msg_type in {"user", "assistant"}:
                    ts = line.get("timestamp")
                    if ts:
                        if started_at is None or ts < started_at:
                            started_at = ts
                        if last_msg_at is None or ts > last_msg_at:
                            last_msg_at = ts

                    message_count += 1
                    if msg_type == "user":
                        user_msg_count += 1
                        if first_user_prompt is None:
                            content = line.get("message", {}).get("content", "")
                            text = _extract_text(content)
                            first_user_prompt = text[:500] if text else None
                    else:
                        assistant_msg_count += 1

    except OSError as exc:
        log.warning("claude_conversations: could not read %s: %s", jsonl_path, exc)
        return None

    if started_at is None:
        return None

    return {
        "session_id": session_id,
        "project_slug": jsonl_path.parent.name,
        "started_at": started_at,
        "last_msg_at": last_msg_at or started_at,
        "message_count": message_count,
        "user_msg_count": user_msg_count,
        "assistant_msg_count": assistant_msg_count,
        "first_user_prompt": first_user_prompt,
    }


def backfill(t: Tracker, start, end) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    projects_root = _claude_root()
    if not projects_root.exists():
        log.info("claude_conversations: %s not found, skipping", projects_root)
        return

    cursor_raw = t.cursor.get(default="0")
    try:
        cursor_mtime = float(cursor_raw)
    except (TypeError, ValueError):
        cursor_mtime = 0.0

    max_mtime = cursor_mtime
    rows = []

    for project_dir in sorted(projects_root.iterdir()):
        if not project_dir.is_dir():
            continue
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
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
        t.upsert("claude_sessions", rows, key=["session_id"])

    t.cursor.set(str(max_mtime))
    log.info("claude_conversations: ingested %d session(s)", len(rows))
