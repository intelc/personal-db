from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_db.templates.trackers.code_agent_activity import actions


@pytest.fixture
def cfg(tmp_path: Path) -> SimpleNamespace:
    settings = tmp_path / "settings.json"
    return SimpleNamespace(
        claude_settings_path=settings,
        hook_command="personal-db code-agent-hook-write",
    )


def test_install_creates_settings_when_missing(cfg: SimpleNamespace) -> None:
    result = actions.install_hooks(cfg)
    assert result["ok"] is True

    data = json.loads(cfg.claude_settings_path.read_text())
    assert "hooks" in data
    for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PreToolUse", "PostToolUse"):
        assert event in data["hooks"]


def test_install_preserves_existing_user_hooks(cfg: SimpleNamespace) -> None:
    cfg.claude_settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "echo user-hook"}]}
                    ]
                }
            }
        )
    )

    actions.install_hooks(cfg)

    data = json.loads(cfg.claude_settings_path.read_text())
    user_hook_present = any(
        h.get("command") == "echo user-hook"
        for entry in data["hooks"]["SessionStart"]
        for h in entry.get("hooks", [])
    )
    assert user_hook_present


def test_install_is_idempotent(cfg: SimpleNamespace) -> None:
    actions.install_hooks(cfg)
    first = cfg.claude_settings_path.read_text()
    actions.install_hooks(cfg)
    second = cfg.claude_settings_path.read_text()
    assert first == second  # exact same bytes — no duplicate entries


def test_uninstall_removes_only_managed_entries(cfg: SimpleNamespace) -> None:
    cfg.claude_settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "echo user-hook"}]}
                    ]
                }
            }
        )
    )
    actions.install_hooks(cfg)
    actions.uninstall_hooks(cfg)

    data = json.loads(cfg.claude_settings_path.read_text())
    # User hook still there
    remaining = data["hooks"].get("SessionStart", [])
    assert any(
        h.get("command") == "echo user-hook"
        for entry in remaining
        for h in entry.get("hooks", [])
    )
    # Our managed hooks gone
    assert not any(
        h.get("_personal_db_managed")
        for entry in remaining
        for h in entry.get("hooks", [])
    )


def test_verify_reports_installed(cfg: SimpleNamespace) -> None:
    actions.install_hooks(cfg)
    result = actions.verify_hooks(cfg)
    assert result["installed"] is True
    assert result["ours_present"] is True


def test_verify_reports_missing_when_absent(cfg: SimpleNamespace) -> None:
    result = actions.verify_hooks(cfg)
    assert result["ours_present"] is False


def test_install_refuses_malformed_settings(cfg: SimpleNamespace) -> None:
    cfg.claude_settings_path.write_text("not json at all {")
    result = actions.install_hooks(cfg)
    assert result["ok"] is False
    assert "malformed" in result["message"].lower() or "parse" in result["message"].lower()
    # File untouched
    assert cfg.claude_settings_path.read_text() == "not json at all {"


def test_install_hooks_blocked_on_scratch_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PERSONAL_DB_ALLOW_GLOBAL_WRITES", raising=False)
    cfg = SimpleNamespace(root=tmp_path)
    result = actions.install_hooks(cfg)
    assert result["ok"] is False
    assert "temp" in result["message"]


def test_uninstall_hooks_blocked_on_scratch_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PERSONAL_DB_ALLOW_GLOBAL_WRITES", raising=False)
    cfg = SimpleNamespace(root=tmp_path)
    result = actions.uninstall_hooks(cfg)
    assert result["ok"] is False
    assert "temp" in result["message"]
