"""End-to-end integration tests for the code_agent_activity tracker.

Pattern: install tracker into a tmp root, drop synthetic JSONL inputs,
trigger sync via the daemon HTTP path (POST /api/sync/code_agent_activity),
assert rows land in db.sqlite.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_db.config import Config
from personal_db.daemon.http import build_app
from personal_db.installer import install_template


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, TestClient]:
    cfg = Config(root=tmp_path / "personal_db")
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    install_template(cfg, "code_agent_activity")

    schema = (cfg.trackers_dir / "code_agent_activity" / "schema.sql").read_text()
    con = sqlite3.connect(cfg.db_path)
    con.executescript(schema)
    con.commit()
    con.close()

    # Claude Code hooks log: two synthetic sessions (alpha, beta) with
    # overlapping start times to exercise concurrent-session tracking.
    hooks_log = cfg.state_dir / "code_agent_hooks.jsonl"
    hooks_log.write_text(
        "\n".join(
            [
                json.dumps({"hook_event_name": "SessionStart", "session_id": "alpha", "received_at": "2026-05-09T10:00:00.000+00:00"}),
                json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "alpha", "received_at": "2026-05-09T10:00:05.000+00:00"}),
                json.dumps({"hook_event_name": "Stop", "session_id": "alpha", "received_at": "2026-05-09T10:00:30.000+00:00"}),
                json.dumps({"hook_event_name": "SessionEnd", "session_id": "alpha", "received_at": "2026-05-09T10:01:00.000+00:00"}),
                json.dumps({"hook_event_name": "SessionStart", "session_id": "beta", "received_at": "2026-05-09T10:00:10.000+00:00"}),
                json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "beta", "received_at": "2026-05-09T10:00:12.000+00:00"}),
                json.dumps({"hook_event_name": "Stop", "session_id": "beta", "received_at": "2026-05-09T10:00:40.000+00:00"}),
                "",
            ]
        )
    )
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(hooks_log))
    monkeypatch.delenv("PERSONAL_DB_ROOT", raising=False)

    # Codex sessions: one minimal rollout file. Real Codex uses payload.id
    # (not payload.session_id) — confirmed in Task 3 against real data.
    codex_root = tmp_path / "codex_home" / "sessions" / "2026" / "05" / "09"
    codex_root.mkdir(parents=True)
    (codex_root / "rollout-test.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-05-09T11:00:00.000Z", "type": "session_meta", "payload": {"id": "codex-1", "cwd": "/p"}}),
                json.dumps({"timestamp": "2026-05-09T11:00:02.000Z", "type": "event_msg", "payload": {"type": "user_message", "content": "<r>"}}),
                json.dumps({"timestamp": "2026-05-09T11:00:30.000Z", "type": "event_msg", "payload": {"type": "task_complete"}}),
                "",
            ]
        )
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex_home"))

    app = build_app(cfg)
    return cfg, TestClient(app)


def test_sync_via_daemon_endpoint(env) -> None:
    """All three synthetic sessions (alpha, beta from Claude; codex-1 from Codex)
    should land in code_agent_events after a single sync call."""
    cfg, client = env
    r = client.post("/api/sync/code_agent_activity")
    assert r.status_code == 200, r.text

    con = sqlite3.connect(cfg.db_path)
    sessions = con.execute(
        "SELECT agent, session_id FROM code_agent_events GROUP BY agent, session_id"
    ).fetchall()
    con.close()

    sessions_set = {(a, s) for a, s in sessions}
    assert ("claude_code", "alpha") in sessions_set
    assert ("claude_code", "beta") in sessions_set
    assert ("codex_cli", "codex-1") in sessions_set


def test_concurrent_sessions_have_separate_intervals(env) -> None:
    """Concurrent Claude Code sessions alpha and beta each get their own
    interval rows in code_agent_intervals."""
    cfg, client = env
    r = client.post("/api/sync/code_agent_activity")
    assert r.status_code == 200, r.text

    con = sqlite3.connect(cfg.db_path)
    alpha = con.execute(
        "SELECT COUNT(*) FROM code_agent_intervals WHERE agent='claude_code' AND session_id='alpha'"
    ).fetchone()[0]
    beta = con.execute(
        "SELECT COUNT(*) FROM code_agent_intervals WHERE agent='claude_code' AND session_id='beta'"
    ).fetchone()[0]
    codex = con.execute(
        "SELECT COUNT(*) FROM code_agent_intervals WHERE agent='codex_cli' AND session_id='codex-1'"
    ).fetchone()[0]
    con.close()

    assert alpha > 0
    assert beta > 0
    assert codex > 0, "expected at least one materialized interval for codex_cli/codex-1"


def test_install_hooks_action_via_daemon(env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/trackers/code_agent_activity/actions/install_hooks should write
    a settings.json with Claude Code hook entries.

    The daemon loads actions.py via importlib.util from the *installed* tracker
    dir (not the bundled-template module), so we redirect the action to a tmp
    settings.json by overriding HOME — that makes Path('~/.claude/settings.json')
    resolve to fake_home/.claude/settings.json.
    """
    _, client = env
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    r = client.post("/api/trackers/code_agent_activity/actions/install_hooks")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    settings = fake_home / ".claude" / "settings.json"
    assert settings.exists(), "install_hooks should have created ~/.claude/settings.json"
    body = json.loads(settings.read_text())
    assert "hooks" in body, "settings.json should contain a 'hooks' block"
    assert "SessionStart" in body["hooks"], "SessionStart hook entry should be present"
