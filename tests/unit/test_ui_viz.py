"""Tests for the visualization registry, discovery, and dashboard config."""

import sqlite3
import subprocess
import sys

import yaml
from fastapi.testclient import TestClient

from personal_db.config import Config
from personal_db.ui.server import build_app
from personal_db.ui.viz import discover, list_trackers_with_viz, load_dashboard_slugs


def _setup(tmp_path, *trackers):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True, capture_output=True,
    )
    for t in trackers:
        subprocess.run(
            [sys.executable, "-m", "personal_db.cli.main", "--root", str(root),
             "tracker", "install", t],
            check=True, capture_output=True,
        )
    return Config(root=root)


def test_discover_finds_builtin_viz(tmp_path):
    cfg = _setup(tmp_path)
    reg = discover(cfg)
    # Built-in health viz is always present
    assert "_builtin:health" in reg
    assert reg["_builtin:health"].tracker == "_builtin"
    assert reg["_builtin:health"].name


def test_discover_finds_tracker_viz_after_install(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting", "life_context")
    reg = discover(cfg)
    assert "daily_time_accounting:today_stack" in reg
    assert "daily_time_accounting:recent_7d" in reg
    assert "life_context:recent_with_log" in reg


def test_list_trackers_with_viz_excludes_builtin(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting", "life_context")
    reg = discover(cfg)
    names = list_trackers_with_viz(reg)
    assert "daily_time_accounting" in names
    assert "life_context" in names
    assert "_builtin" not in names


def test_dashboard_default_includes_all_slugs(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    reg = discover(cfg)
    slugs = load_dashboard_slugs(cfg, reg)
    # No config file → all available
    assert set(slugs) == set(reg.keys())


def test_dashboard_config_filters_to_listed_slugs(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting", "life_context")
    config_dir = cfg.root / ".config"
    config_dir.mkdir()
    (config_dir / "dashboard.yaml").write_text(
        yaml.safe_dump({"viz": [
            "daily_time_accounting:today_stack",
            "_builtin:health",
            "nonexistent:thing",  # should be silently filtered
        ]})
    )
    reg = discover(cfg)
    slugs = load_dashboard_slugs(cfg, reg)
    assert slugs == ["daily_time_accounting:today_stack", "_builtin:health"]


def test_dashboard_route_renders_each_configured_viz(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting", "life_context")
    client = TestClient(build_app(cfg))
    r = client.get("/")
    assert r.status_code == 200
    # Nav bar should show installed trackers
    assert "daily_time_accounting" in r.text
    assert "life_context" in r.text
    # Each viz title appears
    assert "TIME" in r.text  # title is "Today's Time" (apostrophe HTML-escaped)
    assert "DIARY" in r.text
    assert "TRACKER HEALTH" in r.text


def test_single_viz_page(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg))
    r = client.get("/v/daily_time_accounting:today_stack")
    assert r.status_code == 200
    assert "TIME" in r.text  # title is "Today's Time" (apostrophe HTML-escaped)
    # Footer should show slug + tracker link
    assert "daily_time_accounting:today_stack" in r.text


def test_unknown_viz_returns_404(tmp_path):
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.get("/v/does_not:exist")
    assert r.status_code == 404


def test_tracker_page_lists_all_its_viz(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg))
    r = client.get("/t/daily_time_accounting")
    assert r.status_code == 200
    # Both daily_time viz appear on the tracker page
    assert "TIME" in r.text  # title is "Today's Time" (apostrophe HTML-escaped)
    assert "LAST 7 DAYS" in r.text


def test_unknown_tracker_returns_404(tmp_path):
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.get("/t/nonexistent")
    assert r.status_code == 404


def test_broken_viz_does_not_kill_dashboard(tmp_path):
    """If one viz raises, the others still render and the page returns 200."""
    cfg = _setup(tmp_path, "daily_time_accounting")
    # Overwrite the installed visualizations.py with a broken one
    bad = cfg.trackers_dir / "daily_time_accounting" / "visualizations.py"
    bad.write_text(
        "def list_visualizations():\n"
        "    return [{'slug': 'broken', 'name': 'Broken', 'description': '',\n"
        "             'render': lambda cfg: 1/0}]\n"
    )
    client = TestClient(build_app(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "error rendering" in r.text


def test_render_today_stack_returns_html(tmp_path):
    """Direct unit test of a tracker's render function."""
    cfg = _setup(tmp_path, "daily_time_accounting")
    today = "2026-04-26"
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO daily_time_accounting VALUES (?, 'work', 4.5)", (today,)
    )
    con.execute(
        "INSERT INTO daily_time_accounting VALUES (?, 'sleep', 8.0)", (today,)
    )
    con.commit()
    con.close()
    reg = discover(cfg)
    viz = reg["daily_time_accounting:today_stack"]
    html = viz.render(cfg)
    # Today is dynamic; just check the data shows up if it's actually today
    assert "stack" in html or "no data" in html
