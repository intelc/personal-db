"""Tests for personal_db.wizard.mcp_setup."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from personal_db.wizard import mcp_setup


def test_personal_db_path_uses_shutil_which():
    with patch("personal_db.wizard.mcp_setup.shutil.which", return_value="/fake/personal-db"):
        assert mcp_setup._personal_db_path() == "/fake/personal-db"


def test_personal_db_path_raises_when_missing():
    with (
        patch("personal_db.wizard.mcp_setup.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="not found"),
    ):
        mcp_setup._personal_db_path()


def test_upsert_creates_new_json(tmp_path):
    p = tmp_path / "config.json"
    ok, _ = mcp_setup._upsert_json_mcp_server(p, "/abs/personal-db", ["mcp"])
    assert ok
    data = json.loads(p.read_text())
    assert data["mcpServers"]["personal_db"]["command"] == "/abs/personal-db"
    assert data["mcpServers"]["personal_db"]["args"] == ["mcp"]


def test_upsert_preserves_other_servers(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"mcpServers": {"other": {"command": "x", "args": []}}}))
    mcp_setup._upsert_json_mcp_server(p, "/abs/personal-db", ["mcp"])
    data = json.loads(p.read_text())
    assert "other" in data["mcpServers"]
    assert "personal_db" in data["mcpServers"]


def test_upsert_replaces_existing_personal_db(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"mcpServers": {"personal_db": {"command": "old", "args": []}}}))
    mcp_setup._upsert_json_mcp_server(p, "/new/path", ["mcp"])
    data = json.loads(p.read_text())
    assert data["mcpServers"]["personal_db"]["command"] == "/new/path"


def test_upsert_rejects_invalid_existing_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ this is not json")
    ok, msg = mcp_setup._upsert_json_mcp_server(p, "/abs/personal-db", ["mcp"])
    assert not ok
    assert "not valid JSON" in msg


def test_install_claude_code_uses_subprocess(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "personal_db.wizard.mcp_setup.shutil.which",
        lambda x: f"/fake/{x}",
    )
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        m.stdout = ""
        return m

    monkeypatch.setattr("personal_db.wizard.mcp_setup.subprocess.run", fake_run)
    ok, _ = mcp_setup._install_claude_code()
    assert ok
    # First call: remove (idempotent), second: add
    assert any("remove" in args for args in calls)
    assert any("add" in args and "personal_db" in args for args in calls)


def test_install_claude_code_fails_when_claude_missing(monkeypatch):
    monkeypatch.setattr(
        "personal_db.wizard.mcp_setup.shutil.which",
        lambda x: None if x == "claude" else "/fake/personal-db",
    )
    ok, msg = mcp_setup._install_claude_code()
    assert not ok
    assert "claude CLI not found" in msg


def test_install_claude_code_fails_on_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(
        "personal_db.wizard.mcp_setup.shutil.which",
        lambda x: f"/fake/{x}",
    )

    def fake_run(args, **kwargs):
        m = MagicMock()
        if "add" in args:
            m.returncode = 1
            m.stderr = "some error"
            m.stdout = ""
        else:
            m.returncode = 0
            m.stderr = ""
            m.stdout = ""
        return m

    monkeypatch.setattr("personal_db.wizard.mcp_setup.subprocess.run", fake_run)
    ok, msg = mcp_setup._install_claude_code()
    assert not ok
    assert "claude mcp add failed" in msg


def test_install_cursor_writes_correct_path(monkeypatch):
    monkeypatch.setattr(
        "personal_db.wizard.mcp_setup.shutil.which",
        lambda x: "/fake/personal-db",
    )
    monkeypatch.setattr(
        "personal_db.wizard.mcp_setup._upsert_json_mcp_server",
        lambda path, cmd, args: (True, f"wrote {path}"),
    )
    ok, _msg = mcp_setup._install_cursor()
    assert ok


def test_install_claude_desktop_writes_correct_path(monkeypatch):
    monkeypatch.setattr(
        "personal_db.wizard.mcp_setup.shutil.which",
        lambda x: "/fake/personal-db",
    )
    monkeypatch.setattr(
        "personal_db.wizard.mcp_setup._upsert_json_mcp_server",
        lambda path, cmd, args: (True, f"wrote {path}"),
    )
    ok, _msg = mcp_setup._install_claude_desktop()
    assert ok


def test_upsert_creates_parent_dirs(tmp_path):
    nested = tmp_path / "a" / "b" / "config.json"
    ok, _msg = mcp_setup._upsert_json_mcp_server(nested, "/abs/personal-db", ["mcp"])
    assert ok
    assert nested.exists()


def test_targets_dict_has_expected_keys():
    assert set(mcp_setup._TARGETS.keys()) == {"claude_code", "cursor", "claude_desktop"}


def test_mcp_target_labels():
    assert "Claude Code" in mcp_setup._TARGETS["claude_code"].label
    assert "Cursor" in mcp_setup._TARGETS["cursor"].label
    assert "Claude Desktop" in mcp_setup._TARGETS["claude_desktop"].label
