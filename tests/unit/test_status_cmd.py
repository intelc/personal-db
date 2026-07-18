"""Phase 2d: `personal-db status` -- daemon/trackers/FDA/MCP at a glance."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from personal_db.cli import status_cmd
from personal_db.core.config import Config
from personal_db.core.daemon_token import ensure_token
from personal_db.core.permissions import PermissionResult
from personal_db.services.daemon import client as dc


@pytest.fixture(autouse=True)
def _isolate_mcp_probes(monkeypatch, tmp_path):
    """Point the MCP json-config probes at empty tmp paths so this test
    never reads the real machine's ~/.cursor/mcp.json etc."""
    monkeypatch.setitem(status_cmd._MCP_CONFIG_PATHS, "cursor", tmp_path / "cursor_mcp.json")
    monkeypatch.setitem(
        status_cmd._MCP_CONFIG_PATHS, "claude_desktop", tmp_path / "claude_desktop.json"
    )
    monkeypatch.setattr(status_cmd, "_mcp_claude_code_configured", lambda: False)


def test_daemon_status_unreachable(monkeypatch):
    monkeypatch.setattr(dc, "health", lambda: (_ for _ in ()).throw(dc.DaemonUnreachable("nope")))
    assert "not running" in status_cmd._daemon_status()


def test_daemon_status_running(monkeypatch):
    monkeypatch.setattr(dc, "health", lambda: {"status": "ok", "uptime_seconds": 42})
    assert "running" in status_cmd._daemon_status()
    assert "42" in status_cmd._daemon_status()


def test_daemon_status_erroring(monkeypatch):
    monkeypatch.setattr(dc, "health", lambda: (_ for _ in ()).throw(dc.DaemonError("500 boom")))
    assert "erroring" in status_cmd._daemon_status()


def test_token_status_absent(tmp_root):
    cfg = Config(root=tmp_root)
    assert "not yet generated" in status_cmd._token_status(cfg)


def test_token_status_present(tmp_root):
    cfg = Config(root=tmp_root)
    ensure_token(cfg)
    assert status_cmd._token_status(cfg) == "present"


def test_tracker_summary_none_installed(tmp_root):
    cfg = Config(root=tmp_root)
    lines = status_cmd._tracker_summary(cfg)
    assert lines == ["no trackers installed"]


def test_tracker_summary_reports_never_synced_and_age(tmp_root):
    cfg = Config(root=tmp_root)
    (cfg.trackers_dir / "habits").mkdir(parents=True)
    (cfg.trackers_dir / "imessage").mkdir(parents=True)
    six_hours_ago = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
    (cfg.state_dir / "last_run.json").write_text(json.dumps({"imessage": six_hours_ago}))
    lines = status_cmd._tracker_summary(cfg)
    assert lines[0] == "2 installed"
    body = "\n".join(lines)
    assert "habits" in body and "never synced" in body
    assert "imessage" in body and "6.0h ago" in body


def test_fda_summary_reports_granted_and_denied(monkeypatch):
    def fake_probe(path):
        # FDA_PROBES["screen_time"] points at .../Knowledge/knowledgeC.db
        return PermissionResult(granted="Knowledge" in str(path), reason="test")

    monkeypatch.setattr(status_cmd, "probe_sqlite_access", fake_probe)
    lines = status_cmd._fda_summary()
    by_tracker = {line.split()[0]: line for line in lines}
    assert "granted" in by_tracker["screen_time"]
    assert "denied" in by_tracker["imessage"]


def test_mcp_summary_reports_not_configured_by_default():
    lines = status_cmd._mcp_summary()
    body = "\n".join(lines)
    assert "not configured" in body
    assert "mcp install" in body


def test_mcp_json_configured_true_when_personal_db_present(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"personal_db": {"command": "x"}}}))
    assert status_cmd._mcp_json_configured(path) is True


def test_mcp_json_configured_false_when_missing(tmp_path):
    assert status_cmd._mcp_json_configured(tmp_path / "nope.json") is False


def test_status_command_runs_end_to_end(tmp_root, monkeypatch):
    """Smoke test: the full `status()` function runs without raising and
    prints all four sections, with every external dependency mocked."""
    monkeypatch.setattr(dc, "health", lambda: (_ for _ in ()).throw(dc.DaemonUnreachable("x")))
    monkeypatch.setattr(
        status_cmd,
        "probe_sqlite_access",
        lambda path: PermissionResult(granted=False, reason="not on this OS"),
    )
    monkeypatch.setattr("personal_db.cli.status_cmd.get_root", lambda: tmp_root)

    from typer.testing import CliRunner

    from personal_db.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["--root", str(tmp_root), "status"])
    assert result.exit_code == 0, result.output
    assert "daemon" in result.output
    assert "trackers" in result.output
    assert "full disk access" in result.output
    assert "mcp targets" in result.output
