import threading
import time
from unittest.mock import patch

import yaml

from personal_db.core.background_jobs import DeclaredBackgroundJob
from personal_db.core.config import Config
from personal_db.core.manifest import BackgroundJobSpec
from personal_db.services.daemon import server as ds


def test_start_periodic_sync_invokes_sync_due_repeatedly(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def fake_sync_due(c):
        calls.append(time.time())
        return {}

    stop = threading.Event()
    with patch("personal_db.services.daemon.server.sync_due", side_effect=fake_sync_due):
        thread = ds.start_periodic_sync(cfg, interval_seconds=0.05, stop_event=stop)
        time.sleep(0.18)  # enough for ~3 ticks
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, f"expected periodic ticks, got {len(calls)}"
    assert not thread.is_alive()


def test_start_periodic_sync_swallows_errors_and_continues(tmp_root, caplog):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def flaky(c):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {}

    stop = threading.Event()
    with (
        caplog.at_level("ERROR", logger="personal_db.services.daemon"),
        patch("personal_db.services.daemon.server.sync_due", side_effect=flaky),
    ):
        thread = ds.start_periodic_sync(cfg, interval_seconds=0.05, stop_event=stop)
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, "thread should keep going after a single error"
    assert any("periodic sync_due failed" in rec.message for rec in caplog.records), \
        "the swallowed exception should have been logged"


def _make_job_tracker(tmp_root, name="jobbed_tracker") -> DeclaredBackgroundJob:
    """Install a minimal tracker declaring one background job, calling jobs.py:run."""
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
                    {"name": "run", "every": "1h", "entrypoint": "jobs:run"},
                ],
            }
        )
    )
    (d / "jobs.py").write_text("def run(cfg):\n    return {'ran': True}\n")
    return DeclaredBackgroundJob(
        extension_kind="tracker",
        extension_name=name,
        base_dir=d,
        spec=BackgroundJobSpec(name="run", every="1h", entrypoint="jobs:run"),
    )


def test_start_periodic_background_job_invokes_entrypoint_repeatedly(tmp_root):
    cfg = Config(root=tmp_root)
    job = _make_job_tracker(tmp_root)
    calls = []

    def fake_run(c):
        calls.append((c, time.time()))
        return {"ran": True}

    stop = threading.Event()
    # load_entrypoint reloads the job module fresh on every tick, so the
    # simplest way to observe calls is to monkeypatch the loader itself
    # rather than the on-disk jobs.py module.
    with patch("personal_db.services.daemon.server.load_entrypoint", return_value=fake_run) as loader:
        thread = ds.start_periodic_background_job(
            cfg, job, interval_seconds=0.05, stop_event=stop
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, f"expected periodic ticks, got {len(calls)}"
    assert all(call[0] is cfg for call in calls)
    assert loader.call_args.kwargs["modname_prefix"] == "pdb_job_tracker_jobbed_tracker"
    assert not thread.is_alive()


def test_start_periodic_background_job_swallows_errors_and_continues(tmp_root, caplog):
    cfg = Config(root=tmp_root)
    job = _make_job_tracker(tmp_root)
    calls = []

    def flaky(c):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {"ran": True}

    stop = threading.Event()
    with (
        caplog.at_level("ERROR", logger="personal_db.services.daemon"),
        patch("personal_db.services.daemon.server.load_entrypoint", return_value=flaky),
    ):
        thread = ds.start_periodic_background_job(
            cfg, job, interval_seconds=0.05, stop_event=stop
        )
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, "thread should keep going after a single error"
    assert any(
        f"background job {job.qualified_name} failed" in rec.message for rec in caplog.records
    ), "the swallowed exception should have been logged"


def test_start_periodic_background_job_writes_audit_log_rows(tmp_root):
    """Phase 2b: every background-job tick gets an action_log row
    (surface='background_job'), result updated to 'ok'/'error: ...'
    after the entrypoint runs."""
    import sqlite3

    cfg = Config(root=tmp_root)
    job = _make_job_tracker(tmp_root, name="audited_tracker")
    stop = threading.Event()

    thread = ds.start_periodic_background_job(cfg, job, interval_seconds=0.05, stop_event=stop)
    time.sleep(0.12)
    stop.set()
    thread.join(timeout=2.0)

    con = sqlite3.connect(cfg.db_path)
    rows = con.execute(
        "SELECT surface, extension, action, result FROM action_log ORDER BY id"
    ).fetchall()
    con.close()
    assert len(rows) >= 1
    for surface, extension, action, result in rows:
        assert surface == "background_job"
        assert extension == "tracker:audited_tracker"
        assert action == "run"
        assert result == "ok"


def test_start_declared_background_jobs_discovers_and_schedules(tmp_root):
    cfg = Config(root=tmp_root)
    _make_job_tracker(tmp_root, name="tracker_a")
    _make_job_tracker(tmp_root, name="tracker_b")
    scheduled = []

    def fake_start(c, job, *, stop_event=None):
        scheduled.append(job.qualified_name)
        return threading.Thread(target=lambda: None)

    with patch("personal_db.services.daemon.server.start_periodic_background_job", side_effect=fake_start):
        threads = ds.start_declared_background_jobs(cfg)

    assert sorted(scheduled) == ["tracker:tracker_a:run", "tracker:tracker_b:run"]
    assert len(threads) == 2


def test_run_starts_sync_and_declared_jobs(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    sync_calls = []
    job_calls = []
    activate_calls = []

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self):
            return None

    def fake_start_sync(c, *, interval_seconds):
        sync_calls.append((c, interval_seconds))

    def fake_start_jobs(c):
        job_calls.append(c)

    monkeypatch.setattr(ds, "start_periodic_sync", fake_start_sync)
    monkeypatch.setattr(ds, "start_declared_background_jobs", fake_start_jobs)
    monkeypatch.setattr(ds.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(ds, "activate_lib_dir", lambda c: activate_calls.append(c))

    ds.run(cfg, port=9876, interval_seconds=600)

    assert sync_calls == [(cfg, 600)]
    assert job_calls == [cfg]
    assert activate_calls == [cfg], (
        "daemon run() must call activate_lib_dir so <root>/lib (pack python_deps) "
        "is importable before syncs/jobs start"
    )
