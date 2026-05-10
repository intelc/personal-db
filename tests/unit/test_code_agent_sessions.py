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
