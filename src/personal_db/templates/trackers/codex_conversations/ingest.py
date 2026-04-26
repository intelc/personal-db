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


def _history_path() -> Path:
    return Path(os.environ.get("CODEX_HISTORY_FILE") or "~/.codex/history.jsonl").expanduser()


def _load_history_first_prompts() -> dict[str, str]:
    """Map session_id → first user prompt text from ~/.codex/history.jsonl.

    history.jsonl rows: {"session_id": "<uuid>", "ts": <epoch_seconds>, "text": "..."}
    Lines for the same session_id are in chronological order; keep the FIRST one.
    """
    path = _history_path()
    out: dict[str, str] = {}
    if not path.exists():
        return out
    with path.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = d.get("session_id")
            text = d.get("text")
            if sid and text and sid not in out:
                out[sid] = text[:500]
    return out


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


def _extract_text_from_content(content) -> str:
    """Codex stores message content as a list of {type, text} blocks, but
    sometimes serializes it as a stringified Python repr. Handle both."""
    if isinstance(content, str):
        s = content.strip()
        if s.startswith("["):
            try:
                content = json.loads(s)
            except json.JSONDecodeError:
                try:
                    content = ast.literal_eval(s)
                except (ValueError, SyntaxError):
                    return s[:500]
        else:
            return s[:500]
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("input_text")
                if t:
                    parts.append(t)
        return " ".join(parts)[:500]
    return str(content)[:500]


def _is_synthetic_user_message(text: str) -> bool:
    return text.startswith("# AGENTS.md instructions for ")


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
                        if role == "user":
                            content = payload.get("content", "")
                            text = _extract_text_from_content(content)
                            if not _is_synthetic_user_message(text):
                                event_count += 1
                                user_msg_count += 1
                                if first_user_prompt is None:
                                    first_user_prompt = text[:500] if text else None
                        else:
                            event_count += 1
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

    history_prompts = _load_history_first_prompts()

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
            # Override first_user_prompt with history-sourced text if available
            if session["session_id"] in history_prompts:
                session["first_user_prompt"] = history_prompts[session["session_id"]]
            rows.append(session)

    if rows:
        t.upsert("codex_sessions", rows, key=["session_id"])

    t.cursor.set(str(max_mtime))
    log.info("codex_conversations: ingested %d session(s)", len(rows))
