"""Phase 2c: the agent terminal is gated behind `config.yaml:
agent_terminal.enabled` (default off), and spawns without permission-bypass
flags unless `auto_approve` is also set.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from personal_db.core.config import Config
from personal_db.services.daemon.http import build_app
from tests._agent_terminal_helpers import enable_agent_terminal
from tests._daemon_auth import auth_headers


def _cfg(tmp_root) -> Config:
    cfg = Config(root=tmp_root)
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def test_agent_context_reports_disabled_by_default(tmp_root):
    cfg = _cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/agent/context", params={"path": "/"})
    assert r.status_code == 200
    assert r.json()["agent_terminal_enabled"] is False


def test_agent_context_reports_enabled_once_configured(tmp_root):
    cfg = _cfg(tmp_root)
    enable_agent_terminal(cfg)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/agent/context", params={"path": "/"})
    assert r.status_code == 200
    assert r.json()["agent_terminal_enabled"] is True


def test_list_sessions_403_when_disabled(tmp_root):
    cfg = _cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/agent/sessions")
    assert r.status_code == 403
    assert "agent_terminal.enabled" in r.json()["detail"]


def test_create_session_403_when_disabled(tmp_root):
    cfg = _cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/agent/sessions", json={"cli_type": "claude", "context": {}})
    assert r.status_code == 403


def test_delete_session_403_when_disabled(tmp_root):
    cfg = _cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.delete("/api/agent/sessions/whatever")
    assert r.status_code == 403


def test_terminal_ws_403_closes_when_disabled(tmp_root):
    cfg = _cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/agent/sessions/whatever/terminal"):
            pass


def test_create_session_succeeds_once_enabled(tmp_root, monkeypatch):
    monkeypatch.setenv("PERSONAL_DB_CLAUDE_COMMAND", "true")
    cfg = _cfg(tmp_root)
    enable_agent_terminal(cfg)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/agent/sessions", json={"cli_type": "claude", "context": {}})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_session_command_has_no_bypass_flags_when_auto_approve_off(tmp_root, monkeypatch):
    monkeypatch.delenv("PERSONAL_DB_CLAUDE_COMMAND", raising=False)
    cfg = _cfg(tmp_root)
    enable_agent_terminal(cfg, auto_approve=False)

    from personal_db.services.daemon.agent_terminal import AgentTerminalManager

    captured: dict[str, str] = {}
    import subprocess as _subprocess

    real_popen = _subprocess.Popen

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd[-1]  # ["/bin/zsh", "-lc", command]
        return real_popen(["true"], **{k: v for k, v in kwargs.items() if k not in ("stdin", "stdout", "stderr")})

    monkeypatch.setattr(_subprocess, "Popen", fake_popen)
    manager = AgentTerminalManager(cfg)
    session = manager.create(cli_type="claude", context={})
    session.process.wait(timeout=2)

    assert "--permission-mode auto" not in captured["cmd"]
    assert captured["cmd"].startswith("claude ")


def test_session_command_has_bypass_flag_when_auto_approve_on(tmp_root, monkeypatch):
    monkeypatch.delenv("PERSONAL_DB_CLAUDE_COMMAND", raising=False)
    cfg = _cfg(tmp_root)
    enable_agent_terminal(cfg, auto_approve=True)

    from personal_db.services.daemon.agent_terminal import AgentTerminalManager

    captured: dict[str, str] = {}
    import subprocess as _subprocess

    real_popen = _subprocess.Popen

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd[-1]
        return real_popen(["true"], **{k: v for k, v in kwargs.items() if k not in ("stdin", "stdout", "stderr")})

    monkeypatch.setattr(_subprocess, "Popen", fake_popen)
    manager = AgentTerminalManager(cfg)
    session = manager.create(cli_type="claude", context={})
    session.process.wait(timeout=2)

    assert "--permission-mode auto" in captured["cmd"]
