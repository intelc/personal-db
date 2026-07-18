"""`personal-db tracker deps <name>` / `--all` CLI command (cli/tracker_cmd.py).

Reuses the hand-built fixture wheel from tests/_wheel_fixture_helpers.py so
this never touches PyPI -- see that module's docstring for why.
"""

from __future__ import annotations

import typer
import yaml
from typer.testing import CliRunner

from personal_db.cli import tracker_cmd
from personal_db.core.config import Config
from tests._wheel_fixture_helpers import MODULE_NAME, build_fixture_wheel, offline_deps


def _build_app() -> typer.Typer:
    app = typer.Typer()
    app.command("deps")(tracker_cmd.deps)
    return app


def _write_tracker(cfg: Config, name: str, *, python_deps: list[str]) -> None:
    d = cfg.trackers_dir / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": "none",
                "time_column": "ts",
                "python_deps": python_deps,
                "schema": {"tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}},
            }
        )
    )
    (d / "schema.sql").write_text(f"CREATE TABLE IF NOT EXISTS {name} (ts TEXT);")
    (d / "ingest.py").write_text("def sync(t):\n    pass\ndef backfill(t, s, e):\n    pass\n")


def test_deps_reports_no_deps_declared(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    _write_tracker(cfg, "demo", python_deps=[])
    monkeypatch.setattr("personal_db.cli.tracker_cmd.get_root", lambda: root)

    result = CliRunner().invoke(_build_app(), ["demo"])

    assert result.exit_code == 0, result.output
    assert "no python_deps declared" in result.output


def test_deps_installs_declared_dependency(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    wheel_dir = build_fixture_wheel(tmp_path / "wheelhouse")
    deps = offline_deps(wheel_dir)
    _write_tracker(cfg, "demo", python_deps=deps)
    monkeypatch.setattr("personal_db.cli.tracker_cmd.get_root", lambda: root)

    result = CliRunner().invoke(_build_app(), ["demo"])

    assert result.exit_code == 0, result.output
    assert "demo: installed" in result.output
    assert (cfg.lib_dir / MODULE_NAME / "__init__.py").is_file()


def test_deps_all_covers_every_installed_tracker(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    wheel_dir = build_fixture_wheel(tmp_path / "wheelhouse")
    deps = offline_deps(wheel_dir)
    _write_tracker(cfg, "has_deps", python_deps=deps)
    _write_tracker(cfg, "no_deps", python_deps=[])
    monkeypatch.setattr("personal_db.cli.tracker_cmd.get_root", lambda: root)

    result = CliRunner().invoke(_build_app(), ["--all"])

    assert result.exit_code == 0, result.output
    assert "has_deps: installed" in result.output
    assert "no_deps: no python_deps declared" in result.output


def test_deps_rejects_name_and_all_together(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    monkeypatch.setattr("personal_db.cli.tracker_cmd.get_root", lambda: root)
    result = CliRunner().invoke(_build_app(), ["demo", "--all"])
    assert result.exit_code != 0


def test_deps_unknown_tracker_exits_nonzero(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    root.mkdir(parents=True)
    monkeypatch.setattr("personal_db.cli.tracker_cmd.get_root", lambda: root)
    result = CliRunner().invoke(_build_app(), ["nope"])
    assert result.exit_code != 0
