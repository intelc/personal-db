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


def parse_codex_event(line: str, *, source_file: str | None = None) -> dict | None:
    """Stub — implemented in Task 3."""
    raise NotImplementedError
