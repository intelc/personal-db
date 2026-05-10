from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_PY = REPO_ROOT / "src/personal_db/templates/trackers/code_agent_activity/sessions.py"
FIXTURES = REPO_ROOT / "tests/fixtures/code_agent_activity"


def _load_sessions_module():
    spec = importlib.util.spec_from_file_location("_pdb_code_agent_sessions", SESSIONS_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_claude_session_extracts_cwd_from_message_metadata():
    mod = _load_sessions_module()
    jsonl = FIXTURES / "claude_projects/projects/-test-project/abc123.jsonl"
    row = mod.parse_claude_session(jsonl)
    assert row is not None
    assert row["agent"] == "claude_code"
    assert row["session_id"] == "abc123"
    assert row["cwd"] == "/Users/test/code/example"
    assert row["started_at"] == "2026-04-26T10:00:01.000Z"
    assert row["last_msg_at"] == "2026-04-26T10:00:30.000Z"
    assert row["message_count"] == 3
    assert row["user_msg_count"] == 2
    assert row["assistant_msg_count"] == 1
    assert row["first_user_prompt"] == "hello, can you help me debug?"
    assert row["source_file"].endswith("abc123.jsonl")


def test_parse_codex_session_extracts_cwd_and_first_prompt():
    mod = _load_sessions_module()
    jsonl = FIXTURES / "codex_sessions/sessions/2026/04/26/rollout-2026-04-26T10-00-00-550e8400-e29b-41d4-a716-446655440000.jsonl"
    row = mod.parse_codex_session(jsonl, history_map={})
    assert row is not None
    assert row["agent"] == "codex"
    assert row["session_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert row["cwd"] == "/Users/test/code/example"
    assert row["started_at"] == "2026-04-26T10:00:00.000Z"
    assert row["user_msg_count"] == 1
    assert row["assistant_msg_count"] == 1
    assert row["first_user_prompt"] == "Write a hello world in Python"


def test_parse_codex_session_history_overrides_first_prompt():
    mod = _load_sessions_module()
    jsonl = FIXTURES / "codex_sessions/sessions/2026/04/26/rollout-2026-04-26T10-00-00-550e8400-e29b-41d4-a716-446655440000.jsonl"
    row = mod.parse_codex_session(
        jsonl,
        history_map={"550e8400-e29b-41d4-a716-446655440000": "from-history"},
    )
    assert row["first_user_prompt"] == "from-history"


def test_load_codex_history_first_prompts_keeps_first(tmp_path):
    mod = _load_sessions_module()
    history = tmp_path / "history.jsonl"
    history.write_text(
        '{"session_id":"s1","ts":1,"text":"first"}\n'
        '{"session_id":"s1","ts":2,"text":"second"}\n'
        '{"session_id":"s2","ts":3,"text":"only"}\n'
    )
    out = mod.load_codex_history_first_prompts(history)
    assert out == {"s1": "first", "s2": "only"}
