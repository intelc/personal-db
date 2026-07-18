"""`personal-db app deps <name>` / `--all` CLI command (cli/app_cmd.py) --
mirrors test_tracker_cmd_deps.py for the app surface.
"""

from __future__ import annotations

import typer
import yaml
from typer.testing import CliRunner

from personal_db.cli import app_cmd
from personal_db.core.config import Config
from tests._wheel_fixture_helpers import MODULE_NAME, build_fixture_wheel, offline_deps


def _build_app() -> typer.Typer:
    app = typer.Typer()
    app.command("deps")(app_cmd.deps)
    return app


def _write_app(cfg: Config, name: str, *, python_deps: list[str]) -> None:
    d = cfg.apps_dir / name
    d.mkdir(parents=True)
    (d / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "pages": [{"slug": "home", "title": "Home", "view": "render_home"}],
                "python_deps": python_deps,
            }
        )
    )


def test_deps_reports_no_deps_declared(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    _write_app(cfg, "demo_app", python_deps=[])
    monkeypatch.setattr("personal_db.cli.app_cmd.get_root", lambda: root)

    result = CliRunner().invoke(_build_app(), ["demo_app"])

    assert result.exit_code == 0, result.output
    assert "no python_deps declared" in result.output


def test_deps_installs_declared_dependency(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    wheel_dir = build_fixture_wheel(tmp_path / "wheelhouse")
    deps = offline_deps(wheel_dir)
    _write_app(cfg, "demo_app", python_deps=deps)
    monkeypatch.setattr("personal_db.cli.app_cmd.get_root", lambda: root)

    result = CliRunner().invoke(_build_app(), ["demo_app"])

    assert result.exit_code == 0, result.output
    assert "demo_app: installed" in result.output
    assert (cfg.lib_dir / MODULE_NAME / "__init__.py").is_file()


def test_deps_all_covers_every_installed_app(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    wheel_dir = build_fixture_wheel(tmp_path / "wheelhouse")
    deps = offline_deps(wheel_dir)
    _write_app(cfg, "has_deps", python_deps=deps)
    _write_app(cfg, "no_deps", python_deps=[])
    monkeypatch.setattr("personal_db.cli.app_cmd.get_root", lambda: root)

    result = CliRunner().invoke(_build_app(), ["--all"])

    assert result.exit_code == 0, result.output
    assert "has_deps: installed" in result.output
    assert "no_deps: no python_deps declared" in result.output


def test_deps_rejects_name_and_all_together(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    monkeypatch.setattr("personal_db.cli.app_cmd.get_root", lambda: root)
    result = CliRunner().invoke(_build_app(), ["demo_app", "--all"])
    assert result.exit_code != 0


def test_deps_unknown_app_exits_nonzero(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    root.mkdir(parents=True)
    monkeypatch.setattr("personal_db.cli.app_cmd.get_root", lambda: root)
    result = CliRunner().invoke(_build_app(), ["nope"])
    assert result.exit_code != 0
