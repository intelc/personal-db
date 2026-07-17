import yaml

from personal_db.core.apps import install_app_template
from personal_db.core.background_jobs import discover_background_jobs
from personal_db.core.config import Config


def _install_tracker_with_job(tmp_root, name="fixture_tracker"):
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "x",
                "permission_type": "none",
                "setup_steps": [],
                "time_column": "ts",
                "granularity": "event",
                "schema": {"tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}},
                "background_jobs": [
                    {"name": "sweep", "every": "30m", "entrypoint": "jobs:sweep"},
                ],
            }
        )
    )
    (d / "jobs.py").write_text("def sweep(cfg):\n    return {'swept': True}\n")
    return d


def test_discover_background_jobs_finds_tracker_declared_job(tmp_root):
    cfg = Config(root=tmp_root)
    tracker_dir = _install_tracker_with_job(tmp_root)

    jobs = discover_background_jobs(cfg)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.extension_kind == "tracker"
    assert job.extension_name == "fixture_tracker"
    assert job.base_dir == tracker_dir
    assert job.spec.name == "sweep"
    assert job.spec.every == "30m"
    assert job.spec.entrypoint == "jobs:sweep"
    assert job.qualified_name == "tracker:fixture_tracker:sweep"


def test_discover_background_jobs_finds_app_declared_job(tmp_root):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")

    jobs = discover_background_jobs(cfg)

    names = {(j.extension_kind, j.extension_name, j.spec.name) for j in jobs}
    assert ("app", "finance", "enqueue_receipt_v1_jobs") in names
    assert ("app", "finance", "run_due_receipt_v1_jobs") in names
    assert all(j.base_dir == dest for j in jobs if j.extension_name == "finance")


def test_discover_background_jobs_skips_trackers_without_jobs(tmp_root):
    cfg = Config(root=tmp_root)
    d = tmp_root / "trackers" / "plain"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "plain",
                "description": "x",
                "permission_type": "none",
                "setup_steps": [],
                "time_column": "ts",
                "granularity": "event",
                "schema": {"tables": {"plain": {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}},
            }
        )
    )

    jobs = discover_background_jobs(cfg)

    assert jobs == []


def test_discover_background_jobs_ignores_unparseable_manifest(tmp_root):
    cfg = Config(root=tmp_root)
    d = tmp_root / "trackers" / "broken"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("not: [valid, manifest")

    jobs = discover_background_jobs(cfg)

    assert jobs == []
