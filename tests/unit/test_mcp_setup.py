"""Tests for personal_db.services.wizard.mcp_setup."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personal_db.services.wizard import mcp_setup


def test_personal_db_path_uses_shutil_which():
    with patch("personal_db.services.wizard.mcp_setup.shutil.which", return_value="/fake/personal-db"):
        assert mcp_setup._personal_db_path() == "/fake/personal-db"


def test_personal_db_path_raises_when_missing():
    with (
        patch("personal_db.services.wizard.mcp_setup.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="not found"),
    ):
        mcp_setup._personal_db_path()


# --- argv[0] / stable-symlink preference (DMG-user onboarding) ---
#
# These exercise `_personal_db_path()`'s resolution order for the bundled
# Tauri shell's CLI wrapper: prefer the /usr/local/bin/personal-db symlink
# when it's the one pointing at the currently-running binary, else the
# running binary's own resolved path, else fall back to shutil.which(). See
# shell/src-tauri/src/cli_install.rs (creates the symlink) and
# shell/src-tauri/src/mcp_connect.rs (invokes the CLI with the same
# preference, so the two sides agree on which path ends up in a host's MCP
# config).


def _set_argv0(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(sys, "argv", [str(path)] + sys.argv[1:])


def test_resolve_running_cli_path_none_when_argv0_not_personal_db(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/usr/bin/pytest"])
    assert mcp_setup._resolve_running_cli_path() is None


def test_resolve_running_cli_path_none_when_argv0_named_personal_db_but_missing(
    monkeypatch, tmp_path
):
    # Name matches but no file exists there -- e.g. a stale/garbage argv[0].
    _set_argv0(monkeypatch, tmp_path / "personal-db")
    assert mcp_setup._resolve_running_cli_path() is None


def test_resolve_running_cli_path_returns_resolved_argv0(monkeypatch, tmp_path):
    running = tmp_path / "personal-db"
    running.write_text("#!/bin/sh\n")
    _set_argv0(monkeypatch, running)
    assert mcp_setup._resolve_running_cli_path() == str(running.resolve())


def test_personal_db_path_uses_running_argv0_when_no_stable_symlink(monkeypatch, tmp_path):
    running = tmp_path / "personal-db"
    running.write_text("#!/bin/sh\n")
    _set_argv0(monkeypatch, running)
    monkeypatch.setattr(mcp_setup, "_CLI_LINK_PATH", tmp_path / "linkdir" / "personal-db")

    assert mcp_setup._personal_db_path() == str(running.resolve())


def test_personal_db_path_prefers_stable_symlink_when_it_matches(monkeypatch, tmp_path):
    bundle_wrapper = tmp_path / "bundle" / "Contents" / "Resources" / "cli" / "personal-db"
    bundle_wrapper.parent.mkdir(parents=True)
    bundle_wrapper.write_text("#!/bin/sh\n")
    _set_argv0(monkeypatch, bundle_wrapper)

    link_dir = tmp_path / "linkdir"
    link_dir.mkdir()
    link = link_dir / "personal-db"
    link.symlink_to(bundle_wrapper)
    monkeypatch.setattr(mcp_setup, "_CLI_LINK_PATH", link)

    # The stable symlink path wins over the raw bundle path -- this is what
    # a Claude Code/Cursor/Claude Desktop config should end up storing.
    assert mcp_setup._personal_db_path() == str(link)


def test_personal_db_path_ignores_symlink_pointing_elsewhere(monkeypatch, tmp_path):
    running = tmp_path / "bundle-a" / "personal-db"
    running.parent.mkdir(parents=True)
    running.write_text("#!/bin/sh\n")
    _set_argv0(monkeypatch, running)

    other_bundle = tmp_path / "bundle-b" / "personal-db"
    other_bundle.parent.mkdir(parents=True)
    other_bundle.write_text("#!/bin/sh\n")

    link_dir = tmp_path / "linkdir"
    link_dir.mkdir()
    link = link_dir / "personal-db"
    # Symlink points at a *different* (e.g. stale) bundle than the one
    # currently running -- must not be preferred.
    link.symlink_to(other_bundle)
    monkeypatch.setattr(mcp_setup, "_CLI_LINK_PATH", link)

    assert mcp_setup._personal_db_path() == str(running.resolve())


def test_personal_db_path_falls_back_to_which_when_argv0_is_not_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["/usr/bin/pytest"])
    monkeypatch.setattr(mcp_setup, "_CLI_LINK_PATH", tmp_path / "linkdir" / "personal-db")
    monkeypatch.setattr(
        "personal_db.services.wizard.mcp_setup.shutil.which",
        lambda x: "/fake/personal-db" if x == "personal-db" else None,
    )
    assert mcp_setup._personal_db_path() == str(Path("/fake/personal-db").resolve())


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
        "personal_db.services.wizard.mcp_setup.shutil.which",
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

    monkeypatch.setattr("personal_db.services.wizard.mcp_setup.subprocess.run", fake_run)
    ok, _ = mcp_setup._install_claude_code()
    assert ok
    # First call: remove (idempotent), second: add
    assert any("remove" in args for args in calls)
    assert any("add" in args and "personal_db" in args for args in calls)


def test_install_claude_code_fails_when_claude_missing(monkeypatch):
    monkeypatch.setattr(
        "personal_db.services.wizard.mcp_setup.shutil.which",
        lambda x: None if x == "claude" else "/fake/personal-db",
    )
    ok, msg = mcp_setup._install_claude_code()
    assert not ok
    assert "claude CLI not found" in msg


def test_install_claude_code_fails_on_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(
        "personal_db.services.wizard.mcp_setup.shutil.which",
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

    monkeypatch.setattr("personal_db.services.wizard.mcp_setup.subprocess.run", fake_run)
    ok, msg = mcp_setup._install_claude_code()
    assert not ok
    assert "claude mcp add failed" in msg


def test_install_cursor_writes_correct_path(monkeypatch):
    monkeypatch.setattr(
        "personal_db.services.wizard.mcp_setup.shutil.which",
        lambda x: "/fake/personal-db",
    )
    monkeypatch.setattr(
        "personal_db.services.wizard.mcp_setup._upsert_json_mcp_server",
        lambda path, cmd, args: (True, f"wrote {path}"),
    )
    ok, _msg = mcp_setup._install_cursor()
    assert ok


def test_install_claude_desktop_writes_correct_path(monkeypatch):
    monkeypatch.setattr(
        "personal_db.services.wizard.mcp_setup.shutil.which",
        lambda x: "/fake/personal-db",
    )
    monkeypatch.setattr(
        "personal_db.services.wizard.mcp_setup._upsert_json_mcp_server",
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
