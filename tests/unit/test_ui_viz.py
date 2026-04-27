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


def test_refresh_button_renders_on_tracker_page(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg))
    r = client.get("/t/daily_time_accounting")
    assert r.status_code == 200
    # Form posts to /sync/<tracker>; button has the visible label.
    assert 'action="/sync/daily_time_accounting"' in r.text
    assert "↻ refresh" in r.text


def test_refresh_button_skipped_for_builtin_viz(tmp_path):
    """The health viz lives under _builtin — no underlying tracker, no refresh button."""
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg))
    r = client.get("/v/_builtin:health")
    assert r.status_code == 200
    # Health is built-in; no sync to run.
    assert "/sync/_builtin" not in r.text


def test_refresh_endpoint_runs_sync_and_redirects(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg))
    # Track that sync_one was called by checking the framework's last_run.json
    # gets updated after the POST.
    from datetime import datetime, timezone
    before = datetime.now(timezone.utc).isoformat()
    r = client.post(
        "/sync/daily_time_accounting",
        headers={"referer": "/t/daily_time_accounting"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/t/daily_time_accounting"
    # Confirm sync actually ran — last_run.json should now have an entry
    last_run_path = cfg.state_dir / "last_run.json"
    assert last_run_path.exists()
    import json
    data = json.loads(last_run_path.read_text())
    assert "daily_time_accounting" in data
    assert data["daily_time_accounting"] >= before


def test_refresh_endpoint_swallows_sync_errors(tmp_path):
    """A failing sync (e.g. uninstalled tracker) should still redirect, not 500."""
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.post("/sync/nonexistent_tracker", follow_redirects=False)
    assert r.status_code == 303  # not 500


def test_nav_split_under_limit_returns_all_visible():
    from personal_db.ui.server import _split_nav
    visible, overflow = _split_nav(["a", "b", "c"], active=None, limit=6)
    assert visible == ["a", "b", "c"]
    assert overflow == []


def test_nav_split_over_limit_pushes_extras_to_dropdown():
    from personal_db.ui.server import _split_nav
    trackers = ["a", "b", "c", "d", "e", "f", "g", "h"]
    visible, overflow = _split_nav(trackers, active=None, limit=6)
    assert visible == ["a", "b", "c", "d", "e", "f"]
    assert overflow == ["g", "h"]


def test_nav_split_swaps_active_into_visible():
    """If the active tracker would be hidden in overflow, swap it into the
    last visible slot so the highlighted tab stays on screen."""
    from personal_db.ui.server import _split_nav
    trackers = ["a", "b", "c", "d", "e", "f", "g", "h"]
    visible, overflow = _split_nav(trackers, active="h", limit=6)
    assert "h" in visible
    assert "h" not in overflow
    # The displaced tracker (last of original visible) is now in overflow
    assert "f" in overflow


def test_nav_split_active_already_visible_unchanged():
    from personal_db.ui.server import _split_nav
    trackers = ["a", "b", "c", "d", "e", "f", "g", "h"]
    visible, overflow = _split_nav(trackers, active="b", limit=6)
    assert visible == ["a", "b", "c", "d", "e", "f"]
    assert overflow == ["g", "h"]


def test_nav_overflow_renders_in_dashboard_html(tmp_path):
    """End-to-end: install enough trackers to exceed the limit; the rendered
    page should include a 'more' dropdown with the overflow links."""
    from personal_db.installer import list_bundled
    cfg = _setup(tmp_path, *list_bundled())
    client = TestClient(build_app(cfg))
    r = client.get("/")
    assert r.status_code == 200
    # The "more" summary appears
    assert "more ▾" in r.text
    # Dropdown menu container is present
    assert "nav-more-menu" in r.text


def test_every_bundled_tracker_viz_renders_without_error(tmp_path):
    """Smoke test: install every bundled tracker, then render every declared viz.

    Catches typos, missing tables, wrong import names, etc. — any viz that
    raises during render() fails the test with the slug + exception.
    """
    from personal_db.installer import list_bundled
    cfg = _setup(tmp_path, *list_bundled())
    reg = discover(cfg)
    failures = []
    for slug, viz in reg.items():
        try:
            html = viz.render(cfg)
        except Exception as e:  # noqa: BLE001
            failures.append(f"{slug}: {type(e).__name__}: {e}")
            continue
        assert isinstance(html, str), f"{slug} returned non-string"
        assert html, f"{slug} returned empty"
    assert not failures, "viz render failures:\n  " + "\n  ".join(failures)


def _strip_viz_file(cfg, tracker):
    """Helper: simulate a tracker without visualizations.py by deleting it."""
    p = cfg.trackers_dir / tracker / "visualizations.py"
    if p.exists():
        p.unlink()


def test_synthesized_recent_viz_for_trackers_without_viz_file(tmp_path):
    """A tracker without visualizations.py should still get a synthesized :recent."""
    cfg = _setup(tmp_path, "github_commits")
    _strip_viz_file(cfg, "github_commits")
    reg = discover(cfg)
    assert "github_commits:recent" in reg
    assert reg["github_commits:recent"].auto is True
    # And it appears in the nav
    assert "github_commits" in list_trackers_with_viz(reg)


def test_explicit_viz_suppresses_synthesized(tmp_path):
    """daily_time_accounting ships its own viz → no synthesized :recent added."""
    cfg = _setup(tmp_path, "daily_time_accounting")
    reg = discover(cfg)
    assert "daily_time_accounting:today_stack" in reg
    assert "daily_time_accounting:recent" not in reg


def test_dashboard_default_excludes_auto_viz(tmp_path):
    """Synthetic :recent rows shouldn't clutter the dashboard by default."""
    cfg = _setup(tmp_path, "github_commits", "daily_time_accounting")
    _strip_viz_file(cfg, "github_commits")  # force synthesized
    reg = discover(cfg)
    slugs = load_dashboard_slugs(cfg, reg)
    # Curated viz appear
    assert "daily_time_accounting:today_stack" in slugs
    assert "_builtin:health" in slugs
    # Auto-synthesized do NOT appear by default
    assert "github_commits:recent" not in slugs


def test_dashboard_config_can_explicitly_include_auto_viz(tmp_path):
    """User can add an auto viz to their dashboard if they want it there."""
    cfg = _setup(tmp_path, "github_commits")
    _strip_viz_file(cfg, "github_commits")  # force synthesized
    config_dir = cfg.root / ".config"
    config_dir.mkdir()
    (config_dir / "dashboard.yaml").write_text(
        yaml.safe_dump({"viz": ["github_commits:recent"]})
    )
    reg = discover(cfg)
    slugs = load_dashboard_slugs(cfg, reg)
    assert slugs == ["github_commits:recent"]


def test_synthesized_viz_renders_recent_rows(tmp_path):
    cfg = _setup(tmp_path, "github_commits")
    _strip_viz_file(cfg, "github_commits")  # force synthesized
    # Seed a couple of rows
    con = sqlite3.connect(cfg.db_path)
    con.execute(
        "INSERT INTO github_commits(sha, repo, committed_at, message, additions, deletions) "
        "VALUES ('abc123', 'me/x', '2026-04-26T12:00:00Z', 'fix: thing', 10, 2)"
    )
    con.execute(
        "INSERT INTO github_commits(sha, repo, committed_at, message, additions, deletions) "
        "VALUES ('def456', 'me/y', '2026-04-25T08:00:00Z', 'feat: stuff', 50, 0)"
    )
    con.commit()
    con.close()
    reg = discover(cfg)
    html = reg["github_commits:recent"].render(cfg)
    # Both rows should appear (newest first)
    assert "abc123" in html
    assert "def456" in html
    # Newest-first ordering: abc (4-26) appears before def (4-25)
    assert html.index("abc123") < html.index("def456")
    # Header line shows count and time range
    assert "2 rows" in html


def test_synthesized_viz_handles_empty_table(tmp_path):
    cfg = _setup(tmp_path, "github_commits")  # installed but no rows
    _strip_viz_file(cfg, "github_commits")
    reg = discover(cfg)
    html = reg["github_commits:recent"].render(cfg)
    assert "no rows" in html.lower()


def test_tracker_page_works_for_tracker_without_explicit_viz(tmp_path):
    """A tracker without visualizations.py should still load /t/<name>."""
    cfg = _setup(tmp_path, "github_commits")
    _strip_viz_file(cfg, "github_commits")
    client = TestClient(build_app(cfg))
    r = client.get("/t/github_commits")
    assert r.status_code == 200
    assert "RECENT" in r.text
    assert "github_commits:recent" in r.text  # slug visible in title


def test_health_viz_links_tracker_names(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    # Need at least one entry in last_run.json so health renders rows
    state = cfg.state_dir
    state.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    (state / "last_run.json").write_text(
        f'{{"daily_time_accounting": "{datetime.now(timezone.utc).isoformat()}"}}'
    )
    client = TestClient(build_app(cfg))
    r = client.get("/v/_builtin:health")
    assert r.status_code == 200
    # Tracker name should be wrapped in a link to /t/<name>
    assert '<a href="/t/daily_time_accounting">daily_time_accounting</a>' in r.text


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
