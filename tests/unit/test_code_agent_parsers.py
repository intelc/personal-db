from __future__ import annotations

import json

from personal_db.templates.trackers.code_agent_activity.parsers import (
    parse_claude_hook_line,
)


def _line(payload: dict) -> str:
    return json.dumps(payload)


def test_session_start_classified() -> None:
    line = _line(
        {
            "hook_event_name": "SessionStart",
            "session_id": "s1",
            "cwd": "/tmp/p",
            "received_at": "2026-05-09T10:00:00.000+00:00",
        }
    )
    ev = parse_claude_hook_line(line)
    assert ev is not None
    assert ev["agent"] == "claude_code"
    assert ev["session_id"] == "s1"
    assert ev["event_type"] == "session_start"
    assert ev["timestamp"] == "2026-05-09T10:00:00.000+00:00"
    assert ev["cwd"] == "/tmp/p"


def test_user_prompt_submit_to_prompt_submitted() -> None:
    line = _line(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "received_at": "2026-05-09T10:00:01.000+00:00"}
    )
    ev = parse_claude_hook_line(line)
    assert ev is not None
    assert ev["event_type"] == "prompt_submitted"


def test_stop_to_awaiting_user() -> None:
    line = _line({"hook_event_name": "Stop", "session_id": "s1", "received_at": "2026-05-09T10:00:05.000+00:00"})
    ev = parse_claude_hook_line(line)
    assert ev is not None
    assert ev["event_type"] == "awaiting_user"


def test_session_end_to_session_ended() -> None:
    line = _line({"hook_event_name": "SessionEnd", "session_id": "s1", "received_at": "2026-05-09T10:01:00.000+00:00"})
    ev = parse_claude_hook_line(line)
    assert ev is not None
    assert ev["event_type"] == "session_ended"


def test_pre_tool_use_dropped() -> None:
    line = _line({"hook_event_name": "PreToolUse", "session_id": "s1", "received_at": "2026-05-09T10:00:03.000+00:00"})
    assert parse_claude_hook_line(line) is None


def test_post_tool_use_dropped() -> None:
    line = _line({"hook_event_name": "PostToolUse", "session_id": "s1", "received_at": "2026-05-09T10:00:04.000+00:00"})
    assert parse_claude_hook_line(line) is None


def test_malformed_returns_none() -> None:
    assert parse_claude_hook_line("not json") is None
    assert parse_claude_hook_line("{}") is None  # missing hook_event_name
    assert parse_claude_hook_line('{"hook_event_name":"SessionStart"}') is None  # missing session_id


def test_raw_field_is_original_line() -> None:
    line = _line({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00"})
    ev = parse_claude_hook_line(line)
    assert ev["raw"] == line
