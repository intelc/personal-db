"""Screen Time visualizations must work without optional Mosspath data."""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_db.core.config import Config


def _setup(tmp_path: Path) -> Config:
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "install",
            "screen_time",
        ],
        check=True,
        capture_output=True,
    )
    return Config(root=root)


def _load_visualizations(cfg: Config):
    path = cfg.trackers_dir / "screen_time" / "visualizations.py"
    name = f"_test_screen_time_viz_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_mac_visualizations_use_imported_screen_time_without_mosspath(tmp_path):
    cfg = _setup(tmp_path)
    now = datetime.now(UTC)
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO screen_time_app_usage(bundle_id, start_at, end_at, seconds) "
        "VALUES (?, ?, ?, ?)",
        (
            "com.apple.Safari",
            (now - timedelta(hours=2)).isoformat(),
            (now - timedelta(hours=1)).isoformat(),
            3600,
        ),
    )
    con.execute(
        "INSERT INTO screen_time_app_names(bundle_id, app_name, resolved_at) VALUES (?, ?, ?)",
        ("com.apple.Safari", "Safari", now.isoformat()),
    )
    con.commit()
    con.close()

    viz = _load_visualizations(cfg)
    # A nonexistent optional source simulates a normal installation without
    # Mosspath. The Mac panels must still render the data this tracker synced.
    viz._MOSSPATH_DB = tmp_path / "missing-events.sqlite"

    top_apps = viz.render_top_apps_mac_30d(cfg)
    split = viz.render_device_split_30d(cfg)
    timeline = viz.render_device_flame_24h(cfg)

    assert "Safari" in top_apps
    assert "macOS Screen Time" in top_apps
    assert "Mac data from macOS Screen Time" in split
    assert "<svg" in timeline
    assert "Safari" in timeline


def test_phone_panel_explains_that_mosspath_is_optional(tmp_path):
    cfg = _setup(tmp_path)
    viz = _load_visualizations(cfg)
    viz._MOSSPATH_DB = tmp_path / "missing-events.sqlite"

    html = viz.render_top_apps_phone_30d(cfg)

    assert "install Mosspath" in html
    assert "events.sqlite not found" not in html


def test_phone_panel_reports_a_stale_optional_source(tmp_path):
    cfg = _setup(tmp_path)
    source = tmp_path / "events.sqlite"
    stale_end = (datetime.now() - timedelta(days=40)).timestamp()
    con = sqlite3.connect(source)
    con.execute(
        "CREATE TABLE screen_time_sessions("
        "platform TEXT, bundle_id TEXT, start_timestamp REAL, "
        "end_timestamp REAL, duration_seconds INTEGER)"
    )
    con.execute(
        "INSERT INTO screen_time_sessions VALUES (?, ?, ?, ?, ?)",
        ("iphone", "com.apple.MobileSMS", stale_end - 60, stale_end, 60),
    )
    con.commit()
    con.close()

    viz = _load_visualizations(cfg)
    viz._MOSSPATH_DB = source

    html = viz.render_top_apps_phone_30d(cfg)

    assert "no iPhone data in last 30 days" in html
    assert "latest iPhone session ended" in html
    assert "iPhone not connected" not in html
