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


import shutil
import sqlite3 as _sqlite3

from personal_db.config import Config
from personal_db.installer import install_template


@pytest.fixture
def cfg_with_code_agent(tmp_path):
    root = tmp_path / "personal_db"
    for sub in ("trackers", "entities", "notes", "state", "state/oauth"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    cfg = Config(root=root)
    install_template(cfg, "code_agent_activity")
    schema_sql = (cfg.trackers_dir / "code_agent_activity" / "schema.sql").read_text()
    con = _sqlite3.connect(cfg.db_path)
    con.executescript(schema_sql)
    con.commit()
    con.close()
    return cfg


def test_sync_populates_code_agent_sessions(cfg_with_code_agent, monkeypatch):
    cfg = cfg_with_code_agent

    # Stage Claude fixture into a tmp claude projects root so mtime is fresh.
    claude_src = FIXTURES / "claude_projects/projects"
    claude_dst = cfg.root / "fake_claude_projects"
    shutil.copytree(claude_src, claude_dst)
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(claude_dst))

    # Stage Codex fixture and point CODEX_HOME at its parent.
    codex_src = FIXTURES / "codex_sessions/sessions"
    codex_home = cfg.root / "fake_codex"
    (codex_home).mkdir()
    shutil.copytree(codex_src, codex_home / "sessions")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    # No history file → first prompts come from rollout JSONL.
    monkeypatch.setenv("CODEX_HISTORY_FILE", str(codex_home / "history.jsonl"))

    # Empty hooks log so the events phase is a no-op for Claude side.
    (cfg.state_dir / "code_agent_hooks.jsonl").write_text("")
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(cfg.state_dir / "code_agent_hooks.jsonl"))

    from personal_db.sync import sync_one
    sync_one(cfg, "code_agent_activity")

    con = _sqlite3.connect(cfg.db_path)
    rows = con.execute(
        "SELECT agent, session_id, cwd, message_count, first_user_prompt "
        "FROM code_agent_sessions ORDER BY agent"
    ).fetchall()
    con.close()
    assert ("claude_code", "abc123", "/Users/test/code/example", 3, "hello, can you help me debug?") in rows
    assert any(
        r[0] == "codex"
        and r[1] == "550e8400-e29b-41d4-a716-446655440000"
        and r[2] == "/Users/test/code/example"
        for r in rows
    )


def test_claude_session_first_prompt_fallback_from_hook_events(cfg_with_code_agent, monkeypatch):
    """Session present in code_agent_events but no JSONL: first_user_prompt
    populated from earliest user_prompt_submit event."""
    import json as _json
    cfg = cfg_with_code_agent
    # Empty Claude project root → no JSONL for session "ghost"
    empty_claude = cfg.root / "empty_claude_projects"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(empty_claude))
    # Empty Codex too
    empty_codex = cfg.root / "empty_codex"
    empty_codex.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(empty_codex))
    monkeypatch.setenv("CODEX_HISTORY_FILE", str(empty_codex / "history.jsonl"))

    # Hook event log with a SessionStart + UserPromptSubmit + Stop for "ghost".
    # Note: parse_claude_hook_line reads `received_at` (not `timestamp`).
    hooks_log = cfg.state_dir / "code_agent_hooks.jsonl"
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(hooks_log))
    hooks_log.write_text("\n".join([
        _json.dumps({
            "hook_event_name": "SessionStart",
            "session_id": "ghost",
            "received_at": "2026-04-26T12:00:00Z",
            "cwd": "/Users/test/elsewhere",
        }),
        _json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": "ghost",
            "received_at": "2026-04-26T12:00:05Z",
            "cwd": "/Users/test/elsewhere",
            "prompt": "fix the leaky abstraction",
        }),
        _json.dumps({
            "hook_event_name": "Stop",
            "session_id": "ghost",
            "received_at": "2026-04-26T12:01:00Z",
            "cwd": "/Users/test/elsewhere",
        }),
        "",
    ]))

    from personal_db.sync import sync_one
    sync_one(cfg, "code_agent_activity")

    con = _sqlite3.connect(cfg.db_path)
    row = con.execute(
        "SELECT cwd, first_user_prompt FROM code_agent_sessions "
        "WHERE agent='claude_code' AND session_id='ghost'"
    ).fetchone()
    con.close()
    assert row is not None, "ghost session row should be created from hook events"
    assert row[0] == "/Users/test/elsewhere"
    assert row[1] == "fix the leaky abstraction"
