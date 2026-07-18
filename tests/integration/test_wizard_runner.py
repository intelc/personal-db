import yaml

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.services.wizard.runner import run_tracker
from personal_db.services.wizard.status import read_status
from tests._validation_helpers import mark_valid
from tests._wheel_fixture_helpers import MODULE_NAME, build_fixture_wheel, offline_deps


def _install_demo_tracker(tmp_root, setup_steps, python_deps=None):
    d = tmp_root / "trackers" / "demo"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "description": "demo",
                "permission_type": "manual" if not setup_steps else "api_key",
                "setup_steps": setup_steps,
                "schedule": None,
                "time_column": "ts",
                "python_deps": python_deps or [],
                "schema": {
                    "tables": {
                        "demo": {
                            "columns": {
                                "id": {"type": "TEXT", "semantic": "id"},
                                "ts": {"type": "TEXT", "semantic": "ts"},
                            }
                        }
                    }
                },
            }
        )
    )
    (d / "schema.sql").write_text("CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY, ts TEXT);")
    (d / "ingest.py").write_text(
        "def backfill(t,start,end): pass\n"
        "def sync(t):\n"
        "    t.upsert('demo', [{'id':'x','ts':'2026-04-26'}], key=['id'])\n"
    )


def test_run_tracker_with_no_setup_steps_runs_test_sync(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install_demo_tracker(tmp_root, [])
    mark_valid(cfg, "demo")
    result = run_tracker(cfg, "demo")
    assert result.success is True
    s = read_status(cfg)["demo"]
    assert s["success"] is True


def test_run_tracker_with_failing_step_records_failure_skips_sync(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install_demo_tracker(
        tmp_root,
        [{"type": "env_var", "name": "DEMO_VAR", "prompt": "demo"}],
    )
    monkeypatch.delenv("DEMO_VAR", raising=False)
    monkeypatch.setattr("personal_db.services.wizard.steps._prompt", lambda *a, **kw: "")
    result = run_tracker(cfg, "demo")
    assert result.success is False
    assert "no value" in result.detail.lower()
    s = read_status(cfg)["demo"]
    assert s["success"] is False


def test_run_tracker_records_test_sync_failure(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install_demo_tracker(tmp_root, [])
    # Replace the ingest with one that raises
    (tmp_root / "trackers" / "demo" / "ingest.py").write_text(
        "def backfill(t,start,end): pass\ndef sync(t):\n    raise RuntimeError('boom')\n"
    )
    mark_valid(cfg, "demo")
    result = run_tracker(cfg, "demo")
    assert result.success is False
    assert "boom" in result.detail
    s = read_status(cfg)["demo"]
    assert s["success"] is False


def test_run_tracker_auto_installs_declared_python_deps(tmp_root):
    """The terminal wizard installs manifest.python_deps into <root>/lib
    before the test sync (core/pack_deps.py) -- a tracker whose ingest.py
    imports a third-party package shouldn't fail its first sync just
    because <root>/lib hasn't been populated yet."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    wheel_dir = build_fixture_wheel(tmp_root / "wheelhouse")
    deps = offline_deps(wheel_dir)
    _install_demo_tracker(tmp_root, [], python_deps=deps)
    (tmp_root / "trackers" / "demo" / "ingest.py").write_text(
        f"import {MODULE_NAME}\n"
        "def backfill(t,start,end): pass\n"
        "def sync(t):\n"
        f"    t.upsert('demo', [{{'id': {MODULE_NAME}.VALUE, 'ts': '2026-04-26'}}], key=['id'])\n"
    )
    mark_valid(cfg, "demo")

    result = run_tracker(cfg, "demo")

    assert result.success is True, result.detail
    assert (cfg.lib_dir / MODULE_NAME / "__init__.py").is_file()


def test_run_tracker_reports_failure_when_deps_install_fails(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    empty_wheel_dir = tmp_root / "empty_wheelhouse"
    empty_wheel_dir.mkdir()
    _install_demo_tracker(
        tmp_root,
        [],
        python_deps=["--no-index", "--find-links", str(empty_wheel_dir), "nonexistent-pkg-xyz"],
    )
    mark_valid(cfg, "demo")

    result = run_tracker(cfg, "demo")

    assert result.success is False
    assert "python_deps install failed" in result.detail
    s = read_status(cfg)["demo"]
    assert s["success"] is False
