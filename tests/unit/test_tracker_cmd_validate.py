"""Phase 2b: `personal-db tracker validate <name>` CLI command."""

from __future__ import annotations

import typer
import yaml
from typer.testing import CliRunner

from personal_db.cli import tracker_cmd
from personal_db.core.config import Config
from personal_db.core.validation import is_validated


def _build_app() -> typer.Typer:
    app = typer.Typer()
    app.command("validate")(tracker_cmd.validate)
    return app


def _write_tracker(cfg: Config, name: str, *, valid: bool = True) -> None:
    d = cfg.trackers_dir / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": "none",
                "setup_steps": [],
                "time_column": "ts",
                "schema": {"tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}},
            }
        )
    )
    if valid:
        (d / "schema.sql").write_text(f"CREATE TABLE IF NOT EXISTS {name} (ts TEXT);")
        (d / "ingest.py").write_text("def sync(t):\n    pass\ndef backfill(t, s, e):\n    pass\n")
    else:
        (d / "ingest.py").write_text("this is not valid python(((")


def test_validate_passes_and_stamps(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    _write_tracker(cfg, "demo")
    monkeypatch.setattr("personal_db.cli.tracker_cmd.get_root", lambda: root)

    runner = CliRunner()
    result = runner.invoke(_build_app(), ["demo"])

    assert result.exit_code == 0, result.output
    assert "validated" in result.output.lower()
    assert is_validated(cfg, "demo", cfg.trackers_dir / "demo") is True


def test_validate_fails_on_broken_tracker(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    _write_tracker(cfg, "broken", valid=False)
    monkeypatch.setattr("personal_db.cli.tracker_cmd.get_root", lambda: root)

    runner = CliRunner()
    result = runner.invoke(_build_app(), ["broken"])

    assert result.exit_code != 0
    assert is_validated(cfg, "broken", cfg.trackers_dir / "broken") is False


def test_validate_unknown_tracker_exits_nonzero(monkeypatch, tmp_path):
    root = tmp_path / "personal_db"
    root.mkdir(parents=True)
    monkeypatch.setattr("personal_db.cli.tracker_cmd.get_root", lambda: root)

    runner = CliRunner()
    result = runner.invoke(_build_app(), ["nope"])

    assert result.exit_code != 0
