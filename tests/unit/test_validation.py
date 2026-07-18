"""Phase 2b: tracker validation gate (core/validation.py) and the sync-time
enforcement it backs (core/sync.py::sync_one/backfill_one)."""

from __future__ import annotations

import pytest
import yaml

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.core.sync import backfill_one, sync_one
from personal_db.core.validation import (
    TrackerNotValidatedError,
    compute_files_hash,
    ensure_validated,
    is_validated,
    record_validation,
    validate_tracker,
)


def _write_tracker(cfg: Config, name: str = "demo") -> None:
    d = cfg.trackers_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": "1h"},
                "time_column": "ts",
                "granularity": "event",
                "schema": {
                    "tables": {
                        name: {
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
    (d / "schema.sql").write_text(
        f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, ts TEXT);"
    )
    (d / "ingest.py").write_text(
        "def backfill(t, start, end):\n"
        "    t.upsert(t.name, [{'id': 'b1', 'ts': '2026-04-01'}], key=['id'])\n"
        "def sync(t):\n"
        "    t.upsert(t.name, [{'id': 's1', 'ts': '2026-04-25'}], key=['id'])\n"
    )


def test_is_validated_false_when_never_stamped(tmp_root):
    cfg = Config(root=tmp_root)
    _write_tracker(cfg)
    assert is_validated(cfg, "demo", cfg.trackers_dir / "demo") is False


def test_record_and_is_validated_round_trip(tmp_root):
    cfg = Config(root=tmp_root)
    _write_tracker(cfg)
    tracker_dir = cfg.trackers_dir / "demo"
    record_validation(cfg, "demo", compute_files_hash(tracker_dir))
    assert is_validated(cfg, "demo", tracker_dir) is True


def test_is_validated_false_after_hand_edit(tmp_root):
    cfg = Config(root=tmp_root)
    _write_tracker(cfg)
    tracker_dir = cfg.trackers_dir / "demo"
    record_validation(cfg, "demo", compute_files_hash(tracker_dir))
    (tracker_dir / "ingest.py").write_text("def sync(t):\n    pass\n")
    assert is_validated(cfg, "demo", tracker_dir) is False


def test_hash_ignores_visualizations_py(tmp_root):
    """visualizations.py is presentation-only and must not affect the hash —
    editing it should not invalidate an existing stamp."""
    cfg = Config(root=tmp_root)
    _write_tracker(cfg)
    tracker_dir = cfg.trackers_dir / "demo"
    (tracker_dir / "visualizations.py").write_text("# v1\n")
    before = compute_files_hash(tracker_dir)
    (tracker_dir / "visualizations.py").write_text("# v2, totally different\n")
    after = compute_files_hash(tracker_dir)
    assert before == after


def test_hash_includes_migrations(tmp_root):
    cfg = Config(root=tmp_root)
    _write_tracker(cfg)
    tracker_dir = cfg.trackers_dir / "demo"
    before = compute_files_hash(tracker_dir)
    migrations = tracker_dir / "migrations"
    migrations.mkdir()
    (migrations / "001_add_col.sql").write_text("ALTER TABLE demo ADD COLUMN x TEXT;")
    after = compute_files_hash(tracker_dir)
    assert before != after


def test_ensure_validated_raises_clear_error_when_unstamped(tmp_root):
    cfg = Config(root=tmp_root)
    _write_tracker(cfg)
    tracker_dir = cfg.trackers_dir / "demo"
    with pytest.raises(TrackerNotValidatedError, match="tracker validate demo"):
        ensure_validated(cfg, "demo", tracker_dir)


def test_ensure_validated_passes_after_stamp(tmp_root):
    cfg = Config(root=tmp_root)
    _write_tracker(cfg)
    tracker_dir = cfg.trackers_dir / "demo"
    record_validation(cfg, "demo", compute_files_hash(tracker_dir))
    ensure_validated(cfg, "demo", tracker_dir)  # must not raise


def test_sync_one_refuses_unvalidated_tracker(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _write_tracker(cfg)
    with pytest.raises(TrackerNotValidatedError):
        sync_one(cfg, "demo")


def test_sync_one_succeeds_after_validate_tracker(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _write_tracker(cfg)
    result = validate_tracker(cfg, "demo")
    assert result["ok"] is True
    sync_one(cfg, "demo")  # must not raise


def test_backfill_one_refuses_unvalidated_tracker(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _write_tracker(cfg)
    with pytest.raises(TrackerNotValidatedError):
        backfill_one(cfg, "demo", start=None, end=None)


def test_sync_one_refuses_after_hand_edit_post_validation(tmp_root):
    """The core scenario: an agent authors + validates a tracker, then a
    human (or another agent) hand-edits ingest.py afterward -- sync must
    refuse until it's re-validated."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _write_tracker(cfg)
    assert validate_tracker(cfg, "demo")["ok"] is True
    sync_one(cfg, "demo")  # fine so far

    (cfg.trackers_dir / "demo" / "ingest.py").write_text(
        "def sync(t):\n    raise RuntimeError('should never run')\n"
        "def backfill(t, start, end):\n    pass\n"
    )
    with pytest.raises(TrackerNotValidatedError):
        sync_one(cfg, "demo")


def test_validate_tracker_reports_failing_checks_without_stamping(tmp_root):
    cfg = Config(root=tmp_root)
    d = cfg.trackers_dir / "broken"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("not: [valid, yaml, :::")
    result = validate_tracker(cfg, "broken")
    assert result["ok"] is False
    assert is_validated(cfg, "broken", d) is False


def test_validate_tracker_unknown_tracker_raises(tmp_root):
    cfg = Config(root=tmp_root)
    with pytest.raises(FileNotFoundError):
        validate_tracker(cfg, "nope")


def test_python_deps_edit_invalidates_validation_stamp(tmp_root):
    """python_deps rides along in the hash automatically (compute_files_hash
    hashes manifest.yaml's raw bytes) -- editing it must invalidate an
    existing stamp exactly like editing any other manifest field would."""
    cfg = Config(root=tmp_root)
    _write_tracker(cfg)
    tracker_dir = cfg.trackers_dir / "demo"
    record_validation(cfg, "demo", compute_files_hash(tracker_dir))
    assert is_validated(cfg, "demo", tracker_dir) is True

    manifest = yaml.safe_load((tracker_dir / "manifest.yaml").read_text())
    manifest["python_deps"] = ["requests>=2.31"]
    (tracker_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest))

    assert is_validated(cfg, "demo", tracker_dir) is False
