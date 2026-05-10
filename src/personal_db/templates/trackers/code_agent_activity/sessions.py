"""Per-session rollup parsers for code_agent_activity.

Loaded as a sibling of parsers.py/intervals.py via the importlib pattern in
ingest.py — see _load_sibling there.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_CLAUDE_SKIP_TYPES = {
    "permission-mode",
    "attachment",
    "file-history-snapshot",
    "system",
    "last-prompt",
    "queue-operation",
}


def claude_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECTS_DIR") or "~/.claude/projects").expanduser()


def codex_history_path() -> Path:
    return Path(os.environ.get("CODEX_HISTORY_FILE") or "~/.codex/history.jsonl").expanduser()


def _claude_extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def parse_claude_session(jsonl_path: Path) -> dict | None:
    """Parse a Claude Code session JSONL into a code_agent_sessions row.

    cwd is taken from the most recent user/assistant line that carries it.
    Returns None if the file has no user/assistant messages.
    """
    started_at = None
    last_msg_at = None
    message_count = 0
    user_msg_count = 0
    assistant_msg_count = 0
    first_user_prompt = None
    cwd = None
    session_id = jsonl_path.stem

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
                if msg_type in _CLAUDE_SKIP_TYPES:
                    continue
                if msg_type not in {"user", "assistant"}:
                    continue

                ts = line.get("timestamp")
                if ts:
                    if started_at is None or ts < started_at:
                        started_at = ts
                    if last_msg_at is None or ts > last_msg_at:
                        last_msg_at = ts

                line_cwd = line.get("cwd")
                if line_cwd:
                    cwd = line_cwd  # latest wins

                message_count += 1
                if msg_type == "user":
                    user_msg_count += 1
                    if first_user_prompt is None:
                        text = _claude_extract_text(line.get("message", {}).get("content", ""))
                        first_user_prompt = text[:500] if text else None
                else:
                    assistant_msg_count += 1
    except OSError as exc:
        log.warning("code_agent_activity: cannot read %s: %s", jsonl_path, exc)
        return None

    if started_at is None:
        return None

    return {
        "agent": "claude_code",
        "session_id": session_id,
        "cwd": cwd,
        "started_at": started_at,
        "last_msg_at": last_msg_at or started_at,
        "message_count": message_count,
        "user_msg_count": user_msg_count,
        "assistant_msg_count": assistant_msg_count,
        "first_user_prompt": first_user_prompt,
        "source_file": str(jsonl_path),
    }
