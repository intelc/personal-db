from typer.testing import CliRunner

import typer

from personal_db.cli import daemon_cmd
from personal_db.services.daemon import install as di


def _build_app() -> typer.Typer:
    app = typer.Typer()
    app.command("install")(daemon_cmd.install)
    app.command("uninstall")(daemon_cmd.uninstall)
    app.command("status")(daemon_cmd.status)
    app.command("restart")(daemon_cmd.restart)
    return app


def test_install_calls_install(monkeypatch, tmp_path):
    called = {}

    def fake_install(root):
        called["root"] = root
        return {"plist": root / "p.plist"}

    monkeypatch.setattr(di, "install", fake_install)
    monkeypatch.setattr("personal_db.cli.daemon_cmd.get_root", lambda: tmp_path)
    runner = CliRunner()
    r = runner.invoke(_build_app(), ["install"])
    assert r.exit_code == 0
    assert called["root"] == tmp_path
    assert "installed" in r.stdout.lower()


def test_uninstall_calls_uninstall(monkeypatch, tmp_path):
    called = {"yes": False}
    monkeypatch.setattr(di, "uninstall", lambda: called.update(yes=True))
    runner = CliRunner()
    r = runner.invoke(_build_app(), ["uninstall"])
    assert r.exit_code == 0
    assert called["yes"]


def test_status_prints_status(monkeypatch):
    monkeypatch.setattr(di, "status", lambda: "loaded\n")
    runner = CliRunner()
    r = runner.invoke(_build_app(), ["status"])
    assert r.exit_code == 0
    assert "loaded" in r.stdout
