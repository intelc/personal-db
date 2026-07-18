"""Phase 3: `core.contract.generate_contract` — the consumer-facing data
contract Markdown doc, generated from installed (or bundled) manifests."""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

from personal_db.cli.main import app
from personal_db.core.config import Config
from personal_db.core.contract import generate_contract

runner = CliRunner()


def _write_tracker(cfg: Config, name: str, **overrides) -> None:
    tracker_dir = cfg.trackers_dir / name
    tracker_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "description": f"{name} tracker",
        "permission_type": "none",
        "setup_steps": [],
        "time_column": "ts",
        "granularity": "event",
        "schema": {
            "tables": {
                name: {
                    "columns": {
                        "id": {"type": "TEXT", "semantic": "row id"},
                        "ts": {"type": "TEXT", "semantic": "ISO-8601 timestamp"},
                    }
                }
            }
        },
    }
    manifest.update(overrides)
    (tracker_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest))


def _write_app(cfg: Config, name: str) -> None:
    app_dir = cfg.apps_dir / name
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "title": name.title(),
                "description": f"{name} app",
                "reads": {"tables": ["sample"]},
                "writes": {"tables": ["sample"], "actions": ["do_thing"]},
                "pages": [{"slug": "home", "title": "Home", "view": "render_home"}],
            }
        )
    )


def _write_source(cfg: Config, name: str) -> None:
    source_dir = cfg.sources_dir / name
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": f"{name} source",
                "provider": "test_provider",
                "capabilities": ["search"],
            }
        )
    )


def test_installed_mode_renders_only_installed_extensions(tmp_root):
    cfg = Config(root=tmp_root)
    _write_tracker(cfg, "widgets", local_only=True, platform=["darwin"])
    _write_app(cfg, "widget_app")
    _write_source(cfg, "widget_source")

    doc = generate_contract(cfg, bundled=False)

    assert "# personal_db data contract" in doc
    assert "Read `db.sqlite` freely" in doc
    assert "Mutations go through the daemon HTTP API (`/api/v1`" in doc
    assert "### `widgets`" in doc
    assert "widgets tracker" in doc
    assert "`id`" in doc and "row id" in doc
    assert "**time_column**: `ts`" in doc
    assert "local_only" in doc
    assert "macOS only" in doc
    assert "### `widget_app`" in doc
    assert "/api/v1/apps/widget_app/actions/<action>" in doc
    assert "### `widget_source`" in doc
    # A bundled-only tracker must not leak into installed-mode output.
    assert "calendar_events" not in doc


def test_bundled_mode_ignores_cfg_and_lists_all_bundled_trackers(tmp_root):
    cfg = Config(root=tmp_root)  # empty root: nothing installed
    doc = generate_contract(cfg, bundled=True)

    assert "### `imessage`" in doc
    assert "### `calendar`" in doc
    assert "### `whoop`" in doc
    assert "## Trackers (2" in doc or "## Trackers (26)" in doc  # sanity: many bundled trackers
    assert "--bundled" in doc  # regeneration hint in the header comment


def test_installed_mode_empty_root_has_no_extensions(tmp_root):
    cfg = Config(root=tmp_root)
    doc = generate_contract(cfg, bundled=False)
    assert "## Trackers (0)" in doc
    assert "## Apps (0)" in doc
    assert "## Sources (0)" in doc
    assert "(none installed)" in doc


def test_core_tables_section_documents_action_log_and_enrichment_jobs(tmp_root):
    cfg = Config(root=tmp_root)
    doc = generate_contract(cfg, bundled=False)
    assert "`action_log`" in doc
    assert "`enrichment_jobs`" in doc
    assert "`tracker_schema_versions`" in doc
    assert "`notes`" in doc


def test_malformed_manifest_is_skipped_not_fatal(tmp_root):
    cfg = Config(root=tmp_root)
    bad_dir = cfg.trackers_dir / "broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "manifest.yaml").write_text("not: [valid, manifest")
    _write_tracker(cfg, "good")

    doc = generate_contract(cfg, bundled=False)
    assert "### `good`" in doc
    assert "### `broken`" not in doc


def test_dev_contract_cli_writes_to_stdout(tmp_path):
    root = tmp_path / "personal_db"
    result = runner.invoke(app, ["--root", str(root), "dev", "contract", "--bundled"])
    assert result.exit_code == 0, result.output
    assert "# personal_db data contract" in result.output
    assert "### `imessage`" in result.output


def test_dev_contract_cli_writes_to_output_path(tmp_path):
    root = tmp_path / "personal_db"
    out = tmp_path / "contract.md"
    result = runner.invoke(
        app, ["--root", str(root), "dev", "contract", "--bundled", "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert "# personal_db data contract" in out.read_text()
