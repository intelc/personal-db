from unittest.mock import patch

import typer
from typer.testing import CliRunner

from personal_db.cli import sync_cmd
from personal_db.daemon import client as dc


def _app() -> typer.Typer:
    app = typer.Typer()
    app.command("sync")(sync_cmd.sync)
    app.command("backfill")(sync_cmd.backfill)
    return app


def test_sync_delegates_to_daemon():
    with patch.object(dc, "sync_one", return_value={"ok": True, "tracker": "demo"}) as m:
        r = CliRunner().invoke(_app(), ["sync", "demo"])
    assert r.exit_code == 0
    m.assert_called_once_with("demo")


def test_sync_due_delegates_to_daemon():
    with patch.object(dc, "sync_due", return_value={"results": {"a": "ok"}}) as m:
        r = CliRunner().invoke(_app(), ["sync", "--due"])
    assert r.exit_code == 0
    m.assert_called_once_with()


def test_sync_unreachable_exits_2_with_directive_message():
    with patch.object(dc, "sync_one", side_effect=dc.DaemonUnreachable("nope")):
        r = CliRunner().invoke(_app(), ["sync", "demo"])
    assert r.exit_code == 2
    assert "daemon install" in r.stderr.lower() or "daemon install" in r.stdout.lower()


def test_backfill_delegates_to_daemon():
    with patch.object(dc, "backfill", return_value={"ok": True}) as m:
        r = CliRunner().invoke(_app(), ["backfill", "demo", "--from", "2026-01-01", "--to", "2026-01-02"])
    assert r.exit_code == 0
    m.assert_called_once_with("demo", "2026-01-01", "2026-01-02")


def test_backfill_unreachable_exits_2():
    with patch.object(dc, "backfill", side_effect=dc.DaemonUnreachable("nope")):
        r = CliRunner().invoke(_app(), ["backfill", "demo"])
    assert r.exit_code == 2


def test_sync_daemon_error_exits_1():
    with patch.object(dc, "sync_one", side_effect=dc.DaemonError("upstream 500")):
        r = CliRunner().invoke(_app(), ["sync", "demo"])
    assert r.exit_code == 1
    assert "daemon error" in r.output


def test_backfill_daemon_error_exits_1():
    with patch.object(dc, "backfill", side_effect=dc.DaemonError("upstream 500")):
        r = CliRunner().invoke(_app(), ["backfill", "demo"])
    assert r.exit_code == 1
    assert "daemon error" in r.output
