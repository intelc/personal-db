import yaml

from personal_db.config import Config
from personal_db.db import init_db
from personal_db.wizard.runner import run_tracker
from personal_db.wizard.status import read_status


def _install_demo_tracker(tmp_root, setup_steps):
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
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
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
    result = run_tracker(cfg, "demo")
    assert result.success is False
    assert "boom" in result.detail
    s = read_status(cfg)["demo"]
    assert s["success"] is False
