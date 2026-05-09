"""Pure-function parsers for the code_agent_activity tracker.

Two entry points:

- parse_claude_hook_line: maps one line of code_agent_hooks.jsonl (written by
  the personal-db code-agent-hook-write CLI) to a normalized event dict, or
  None if the line is malformed or names a hook event we don't classify in v1.

- parse_codex_event: same shape, applied to one JSONL line from a Codex
  rollout file (`event_msg` rows).
"""

from __future__ import annotations

import json

# Maps Claude Code hook_event_name -> our v1 event_type.
# PreToolUse/PostToolUse are intentionally absent; they're forward-compat
# scaffolding (we install the hooks so a future v2 doesn't require re-running
# the installer) but we drop the rows at classification time.
_CLAUDE_EVENT_MAP = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "prompt_submitted",
    "Stop": "awaiting_user",
    "SessionEnd": "session_ended",
}

# Maps Codex rollout event_msg payload.type -> our v1 event_type.
# Verified against real ~/.codex/sessions/2026/05/09/rollout-*.jsonl files.
# agent_message (full assembled message) is the streaming content type — not
# a state transition, so intentionally absent here.
_CODEX_PAYLOAD_MAP = {
    "user_message": "prompt_submitted",
    "task_complete": "awaiting_user",
}


def parse_claude_hook_line(line: str) -> dict | None:
    """Parse one line of code_agent_hooks.jsonl into a normalized event dict.

    Returns None on:
      - malformed JSON
      - missing required fields (hook_event_name, session_id, received_at)
      - hook_event_name not in the v1 classification (PreToolUse, PostToolUse,
        anything unknown).
    """
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    hook_name = payload.get("hook_event_name")
    event_type = _CLAUDE_EVENT_MAP.get(hook_name)
    if event_type is None:
        return None

    session_id = payload.get("session_id")
    timestamp = payload.get("received_at")
    if session_id is None or timestamp is None:
        return None

    return {
        "agent": "claude_code",
        "session_id": str(session_id),
        "timestamp": str(timestamp),
        "event_type": event_type,
        "cwd": payload.get("cwd"),
        "git_branch": payload.get("git_branch"),
        "source_file": None,
        "raw": line.rstrip("\n"),
    }


def parse_codex_event(
    line: str,
    *,
    source_file: str | None = None,
    session_id: str | None = None,
) -> dict | None:
    """Parse one line of a Codex rollout-*.jsonl into a normalized event dict.

    The caller threads `session_id` from the most recent `session_meta` row in
    the same file (the per-line shape doesn't carry it for event_msg rows).

    In real Codex rollout files the session ID lives at `payload.id` (not
    `payload.session_id`). This was confirmed against files emitted by Codex
    Desktop cli_version 0.129.0-alpha.15.

    Returns None on malformed input, on rows that don't represent state
    transitions (streaming content, internal accounting), or when session_id
    is required but missing.
    """
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    timestamp = payload.get("timestamp")
    row_type = payload.get("type")
    inner = payload.get("payload") or {}
    if timestamp is None or row_type is None:
        return None

    if row_type == "session_meta":
        # Real Codex rollout files use payload.id (not payload.session_id).
        sid = inner.get("id")
        if sid is None:
            return None
        return {
            "agent": "codex_cli",
            "session_id": str(sid),
            "timestamp": str(timestamp),
            "event_type": "session_start",
            "cwd": inner.get("cwd"),
            "git_branch": None,
            "source_file": source_file,
            "raw": line.rstrip("\n"),
        }

    if row_type == "event_msg":
        event_type = _CODEX_PAYLOAD_MAP.get(inner.get("type"))
        if event_type is None or session_id is None:
            return None
        return {
            "agent": "codex_cli",
            "session_id": str(session_id),
            "timestamp": str(timestamp),
            "event_type": event_type,
            "cwd": None,
            "git_branch": None,
            "source_file": source_file,
            "raw": line.rstrip("\n"),
        }

    return None
