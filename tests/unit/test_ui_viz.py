"""Tests for the visualization registry and discovery."""

import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.services.daemon.http import build_app
from personal_db.services.ui.viz import discover, list_trackers_with_viz
from tests._daemon_auth import auth_headers


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


@pytest.mark.darwin_only  # installs the darwin-gated life_context tracker
def test_discover_finds_tracker_viz_after_install(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting", "life_context")
    reg = discover(cfg)
    assert "daily_time_accounting:today_stack" in reg
    assert "daily_time_accounting:recent_7d" in reg
    assert "life_context:recent_with_log" in reg


@pytest.mark.darwin_only  # installs the darwin-gated life_context tracker
def test_list_trackers_with_viz_excludes_builtin(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting", "life_context")
    reg = discover(cfg)
    names = list_trackers_with_viz(reg)
    assert "daily_time_accounting" in names
    assert "life_context" in names
    assert "_builtin" not in names


def test_dashboard_shows_welcome_hero_with_no_trackers(tmp_path):
    """A fresh root with zero trackers installed gets a first-run welcome pane, not a bare fallback."""
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "Connect your first source" in r.text
    assert "/setup" in r.text


@pytest.mark.darwin_only  # installs the darwin-gated life_context tracker
def test_life_context_form_exposes_backdated_note_fields(tmp_path):
    cfg = _setup(tmp_path, "life_context")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    # The form lives in the viz body, which the default (non-?full=1) tracker
    # page defers to a placeholder -- ?full=1 restores the old synchronous
    # render so this test can inspect the rendered form fields.
    r = client.get("/t/life_context?full=1")

    assert r.status_code == 200
    assert 'action="/log_life_context"' in r.text
    assert 'name="start_date"' in r.text
    assert 'type="date"' in r.text
    assert "flew to Japan" in r.text


def test_single_viz_page(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/v/daily_time_accounting:today_stack")
    assert r.status_code == 200
    assert "TIME" in r.text  # title is "Today's Time" (apostrophe HTML-escaped)
    # Footer should show slug + tracker link
    assert "daily_time_accounting:today_stack" in r.text


def test_unknown_viz_returns_404(tmp_path):
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/v/does_not:exist")
    assert r.status_code == 404


def test_tracker_page_lists_all_its_viz(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/t/daily_time_accounting")
    assert r.status_code == 200
    # Both daily_time viz appear on the tracker page
    assert "TIME" in r.text  # title is "Today's Time" (apostrophe HTML-escaped)
    assert "LAST 7 DAYS" in r.text


def test_unknown_tracker_returns_404(tmp_path):
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/t/nonexistent")
    assert r.status_code == 404


# ux-pass-4: progressive viz loading. The dashboard/tracker/viz page routes
# default to a `data-viz-src` placeholder per block instead of rendering the
# viz body inline (see http.py); pdb-lazy.js fetches each placeholder's
# fragment client-side. `today_stack`'s render_today_stack (visualizations.py
# above) returns "no data yet for today — run sync" for an empty DB, which
# makes a reliable "the actual viz body rendered here" marker for these
# tests -- it should be ABSENT from a default (placeholder) response and
# PRESENT once fetched via ?full=1 or the fragment endpoint.
_TODAY_STACK_BODY_MARKER = "no data yet for today"


def test_tracker_page_default_defers_viz_to_placeholders(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/t/daily_time_accounting")
    assert r.status_code == 200
    assert 'data-viz-src="/viz/daily_time_accounting:today_stack/html"' in r.text
    assert _TODAY_STACK_BODY_MARKER not in r.text

    full = client.get("/t/daily_time_accounting?full=1")
    assert full.status_code == 200
    assert "viz-pending" not in full.text
    assert _TODAY_STACK_BODY_MARKER in full.text


def test_viz_page_default_defers_to_placeholder(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/v/daily_time_accounting:today_stack")
    assert r.status_code == 200
    assert 'data-viz-src="/viz/daily_time_accounting:today_stack/html"' in r.text
    assert _TODAY_STACK_BODY_MARKER not in r.text

    full = client.get("/v/daily_time_accounting:today_stack?full=1")
    assert full.status_code == 200
    assert "viz-pending" not in full.text
    assert _TODAY_STACK_BODY_MARKER in full.text


def test_viz_fragment_endpoint_returns_rendered_body(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/viz/daily_time_accounting:today_stack/html")
    assert r.status_code == 200
    assert _TODAY_STACK_BODY_MARKER in r.text
    # Just the fragment -- no page chrome.
    assert "<html" not in r.text.lower()
    assert 'id="pdb-sidebar"' not in r.text


def test_viz_fragment_endpoint_unknown_slug_404s(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/viz/does_not:exist/html")
    assert r.status_code == 404


def test_viz_fragment_endpoint_isolates_render_error(tmp_path):
    """A failing render is a 200 with inline error markup, not a 500 --
    same isolation the ?full=1 page routes have always had, just reachable
    per-viz through the fragment endpoint pdb-lazy.js actually calls."""
    cfg = _setup(tmp_path, "daily_time_accounting")
    bad = cfg.trackers_dir / "daily_time_accounting" / "visualizations.py"
    bad.write_text(
        "def list_visualizations():\n"
        "    return [{'slug': 'broken', 'name': 'Broken', 'description': '',\n"
        "             'render': lambda cfg: 1/0}]\n"
    )
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/viz/daily_time_accounting:broken/html")
    assert r.status_code == 200
    assert "error rendering daily_time_accounting:broken" in r.text


def test_refresh_button_renders_on_tracker_page(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/t/daily_time_accounting")
    assert r.status_code == 200
    # Form posts to /sync/<tracker>; button has the visible label. (pdb-sync.js
    # progressively enhances the submit to fetch /api/v1/sync/<tracker> instead.)
    assert 'action="/sync/daily_time_accounting"' in r.text
    assert ">Sync</button>" in r.text


def test_setup_button_renders_on_tracker_and_viz_pages(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    tracker = client.get("/t/daily_time_accounting")
    viz = client.get("/v/daily_time_accounting:today_stack")

    assert tracker.status_code == 200
    assert viz.status_code == 200
    assert 'href="/setup/daily_time_accounting"' in tracker.text
    assert 'href="/setup/daily_time_accounting"' in viz.text
    assert "setup" in tracker.text


def test_refresh_button_skipped_for_builtin_viz(tmp_path):
    """The health viz lives under _builtin — no underlying tracker, no refresh button."""
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/v/_builtin:health")
    assert r.status_code == 200
    # Health is built-in; no sync to run.
    assert "/sync/_builtin" not in r.text
    assert "/setup/_builtin" not in r.text


def test_refresh_endpoint_runs_sync_and_redirects(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    # Track that sync_one was called by checking the framework's last_run.json
    # gets updated after the POST.
    from datetime import UTC, datetime
    before = datetime.now(UTC).isoformat()
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
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/sync/nonexistent_tracker", follow_redirects=False)
    assert r.status_code == 303  # not 500


def test_nav_context_lists_every_tracker_with_humanized_title(tmp_path):
    """Sidebar nav (no overflow dropdown -- the sidebar scrolls instead) lists
    every tracker with a human title, not the raw slug."""
    cfg = _setup(tmp_path, "github_commits", "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/t/github_commits"' in r.text
    assert ">GitHub Commits<" in r.text
    assert 'href="/t/daily_time_accounting"' in r.text
    assert ">Daily Time Accounting<" in r.text


def test_sidebar_footer_shows_status_row_and_setup_gear(tmp_path):
    """Fixed sidebar footer: Health status row (dot + label) and a Setup
    gear icon button, replacing the old plain text links."""
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="sidebar-status"' in r.text
    assert 'href="/health"' in r.text
    assert "All sources syncing" in r.text
    assert 'class="sidebar-status-dot is-ok"' in r.text
    assert 'href="/setup"' in r.text
    assert 'aria-label="Setup"' in r.text


@pytest.mark.darwin_only  # installs every bundled tracker, including darwin-gated ones
def test_nav_context_renders_every_bundled_tracker(tmp_path):
    """End-to-end: install every bundled tracker; every one should show up as
    a sidebar link (no visible-count cap anymore)."""
    from personal_db.core.installer import list_bundled
    cfg = _setup(tmp_path, *list_bundled())
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    for tracker in list_bundled():
        assert f'href="/t/{tracker}"' in r.text


@pytest.mark.darwin_only  # installs every bundled tracker, including darwin-gated ones
def test_every_bundled_tracker_viz_renders_without_error(tmp_path):
    """Smoke test: install every bundled tracker, then render every declared viz.

    Catches typos, missing tables, wrong import names, etc. — any viz that
    raises during render() fails the test with the slug + exception.
    """
    from personal_db.core.installer import list_bundled
    cfg = _setup(tmp_path, *list_bundled())
    reg = discover(cfg)
    failures = []
    for slug, viz in reg.items():
        try:
            html = viz.render(cfg)
        except Exception as e:
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
    # Generic recent tables render through the shared AG Grid bridge.
    assert "data-pdb-grid" in html
    assert "data-pdb-grid-options" in html


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
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/t/github_commits")
    assert r.status_code == 200
    assert "RECENT" in r.text
    assert "github_commits:recent" in r.text  # slug visible in title


def test_health_viz_links_tracker_names(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    # Need at least one entry in last_run.json so health renders rows
    state = cfg.state_dir
    state.mkdir(parents=True, exist_ok=True)
    from datetime import UTC, datetime
    (state / "last_run.json").write_text(
        f'{{"daily_time_accounting": "{datetime.now(UTC).isoformat()}"}}'
    )
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    # The link lives in the viz body, deferred to a placeholder by default;
    # ?full=1 restores the synchronous render this test inspects.
    r = client.get("/v/_builtin:health?full=1")
    assert r.status_code == 200
    # Human title links to /t/<name>; the raw slug stays as a tooltip
    assert (
        '<a href="/t/daily_time_accounting" title="daily_time_accounting">'
        "Daily Time Accounting</a>" in r.text
    )


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


def test_aggrid_table_marks_safe_html_columns():
    from personal_db.ui.aggrid import table_grid

    class SafeHtml(str):
        pass

    html = table_grid(
        [("AT&amp;T", SafeHtml("<span>badge</span>"))],
        ["Name", "Badge"],
        html_columns={1},
    )

    assert "data-pdb-grid" in html
    assert "AT&T" in html
    assert "AT&amp;amp;T" not in html
    assert '"cellRenderer": "html"' in html
    assert "<\\/span>" in html


def test_data_grid_normalizes_dict_rows_with_string_columns():
    from personal_db.ui import components as c

    html = c.data_grid(
        [{"day": "2026-05-31", "place_name": "Studio", "points": 3}],
        ["day", "place_name", "points"],
    )

    assert "data-pdb-grid" in html
    assert '"field": "day"' in html
    assert '"headerName": "Place Name"' in html
    assert '"place_name": "Studio"' in html


def test_data_grid_marks_dict_html_columns():
    from personal_db.ui import components as c

    html = c.data_grid(
        [{"label": "AT&amp;T", "action": "<button>save</button>"}],
        ["label", "action"],
        html_columns={1},
    )

    assert '"field": "action"' in html
    assert '"cellRenderer": "html"' in html
    assert "<button>save<\\/button>" in html


def test_agcharts_line_renders_structured_options():
    from personal_db.ui.agcharts import line_chart

    html = line_chart(
        [("01-01", 10.0), ("01-02", 12.0)],
        color="#111111",
        value_attr="data-usd",
        legend_position="right",
        month_markers=True,
    )

    assert "data-pdb-chart" in html
    assert "data-pdb-chart-options" in html
    assert '"type": "line"' in html
    assert '"axes": {"bottom": {"type": "category"}, "left": {"type": "number"}}' in html
    assert '"legend": {"enabled": false, "position": "right"}' in html
    assert '"pdbZoom": {"enabled": true, "windows": [365, 180, 90, 30, 7]}' in html
    assert '"pdbScale": {"enabled": true, "mode": "auto"' in html
    assert '"pdbTimeMarkers": {"enabled": true, "monthBoundaries": true, "xKey": "x"}' in html


def test_agcharts_gain_loss_area_can_enable_time_grouping():
    from personal_db.ui.agcharts import gain_loss_area_chart

    html = gain_loss_area_chart(
        ["05-23", "05-24", "05-25"],
        [10.0, -12.0, 3.0],
        date_values=["2026-05-23", "2026-05-24", "2026-05-25"],
        extra_values={"income": [20, 0, 10], "spending": [10, 12, 7]},
        tooltip_fields=[
            {"key": "income", "label": "Income", "format": "usd"},
            {"key": "spending", "label": "Spending", "format": "usd"},
        ],
        aggregation=True,
        aggregation_default_mode="week",
        aggregation_sum_keys=["net", "income", "spending"],
        scale_default_mode="full",
        month_markers=True,
        value_attr="data-usd",
    )

    assert '"date": "2026-05-23"' in html
    assert '"pdbAggregation": {"enabled": true' in html
    assert '"dateKey": "date"' in html
    assert '"modes": ["day", "week", "month"]' in html
    assert '"defaultMode": "week"' in html
    assert '"sumKeys": ["net", "income", "spending"]' in html
    assert '"pdbTooltip": {"fields": [{"key": "income", "label": "Income", "format": "usd"}' in html
    assert '"defaultMode": "full"' in html
    assert '"valueFormat": "usd"' in html


def test_agcharts_gain_loss_area_defaults_to_zoom_window():
    from personal_db.ui.agcharts import gain_loss_area_chart

    html = gain_loss_area_chart(
        ["05-23", "05-24", "05-25"],
        [10.0, -12.0, 3.0],
        value_attr="data-usd",
        zoom_default_window=7,
        month_markers=True,
    )

    assert '"type": "area"' in html
    assert '"yKey": "gain"' in html
    assert '"yKey": "loss"' in html
    assert '"type": "line"' in html
    assert '"yKey": "net"' in html
    assert '"legend": {"enabled": false}' in html
    assert '"defaultWindow": 7' in html
    assert '"pdbTimeMarkers": {"enabled": true, "monthBoundaries": true, "xKey": "x"}' in html


def test_base_uses_vendored_ag_assets(tmp_path):
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")

    assert r.status_code == 200
    assert "/static/vendor/ag-grid-community/35.3.0/ag-grid-community.min.js" in r.text
    assert "/static/vendor/ag-charts-community/13.3.0/ag-charts-community.min.js" in r.text
    assert "/static/pdb-grid.js?v=8" in r.text
    assert "/static/pdb-chart.js?v=14" in r.text
    assert "/static/style.css?v=p2-1" in r.text
    assert "/static/pdb-app-state.js?v=3" in r.text
    assert "/static/apps/finance-burn-rate.js?v=4" in r.text
    assert "/static/apps/finance-categorize.js?v=1" in r.text
    assert "/static/apps/finance-rules.js?v=1" in r.text
    assert "/static/pdb-finance.js?v=10" in r.text
    assert "/static/pdb-sync.js?v=4" in r.text
    assert "/static/pdb-nav.js?v=3" in r.text
    assert "/static/pdb-lazy.js?v=1" in r.text
    assert "/static/pdb-tiles.js?v=3" in r.text
    assert "/static/pdb-data.js?v=2" in r.text
    assert "cdn.jsdelivr.net" not in r.text


def test_health_page_lists_installed_tracker_with_humanized_title(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/health")
    assert r.status_code == 200
    assert "Daily Time Accounting" in r.text
    assert 'title="daily_time_accounting"' in r.text
    assert "every 1h" in r.text
    # sidebar Health link present and marked current on this page
    assert 'href="/health"' in r.text
    assert 'aria-current="page"' in r.text


def test_health_page_sync_all_button_renders(tmp_path):
    """Page-level "Sync all due" button (sync_all_btn macro): posts to the
    daemon's sync-everything-due route, and carries [data-sync-all] so
    pdb-sync.js's delegated handler can progressively enhance it."""
    cfg = _setup(tmp_path, "daily_time_accounting")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/health")
    assert r.status_code == 200
    assert 'action="/api/v1/sync_due"' in r.text
    assert "data-sync-all" in r.text
    assert "Sync all due" in r.text


def test_health_page_error_newer_than_success_renders(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    now = datetime.now(timezone.utc)
    success_ts = (now - timedelta(hours=2)).isoformat()
    error_ts = (now - timedelta(minutes=5)).isoformat()

    (cfg.state_dir / "last_run.json").write_text(
        json.dumps({"daily_time_accounting": success_ts})
    )
    with (cfg.state_dir / "sync_errors.jsonl").open("w") as f:
        f.write(
            json.dumps(
                {
                    "ts": error_ts,
                    "tracker": "daily_time_accounting",
                    "error": "boom: connection reset\nmore detail",
                    "tb": "Traceback (most recent call last):\n  ...",
                }
            )
            + "\n"
        )

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/health")
    assert r.status_code == 200
    assert "boom: connection reset" in r.text
    assert "health-row-error" in r.text


def test_health_page_shows_next_sync_countdown_or_due_now(tmp_path):
    """Roadmap item 7 ("Visible schedules"): every-schedule rows show a
    computed next-due time next to the "every Xh" text, clamped to "due
    now" once that instant has passed. daily_time_accounting ships with
    `schedule.every: 1h` (see its manifest.yaml)."""
    cfg = _setup(tmp_path, "daily_time_accounting")
    now = datetime.now(timezone.utc)

    # Synced 10m ago with an hourly schedule -- ~50m still left before due.
    # Match the rendered "~Nm" and check a range rather than an exact value:
    # compute_next_sync floors to the minute, so a request that lands a few
    # milliseconds after `now` above can render 49m instead of 50m.
    not_due_ts = (now - timedelta(minutes=10)).isoformat()
    (cfg.state_dir / "last_run.json").write_text(
        json.dumps({"daily_time_accounting": not_due_ts})
    )
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/health")
    assert r.status_code == 200
    assert "every 1h" in r.text
    match = re.search(r"next in ~(\d+)m", r.text)
    assert match, r.text
    assert 45 <= int(match.group(1)) <= 50

    # Synced 2h ago with an hourly schedule -- clamps to "due now" rather
    # than showing a negative countdown.
    overdue_ts = (now - timedelta(hours=2)).isoformat()
    (cfg.state_dir / "last_run.json").write_text(
        json.dumps({"daily_time_accounting": overdue_ts})
    )
    r = client.get("/health")
    assert r.status_code == 200
    assert "due now" in r.text


def test_setup_overview_card_shows_next_sync_for_every_schedule(tmp_path):
    """The /setup overview card's sync-age line grows a "· next in ~Xh"
    (or "due now") suffix for installed trackers with an `every` schedule,
    matching the /health row's computation (services/ui/builtin_viz.py's
    `compute_next_sync`)."""
    cfg = _setup(tmp_path, "daily_time_accounting")
    now = datetime.now(timezone.utc)
    not_due_ts = (now - timedelta(minutes=10)).isoformat()
    (cfg.state_dir / "last_run.json").write_text(
        json.dumps({"daily_time_accounting": not_due_ts})
    )
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup")
    assert r.status_code == 200
    assert "Synced" in r.text
    # See the /health equivalent test for why this checks a range rather
    # than an exact "~50m" -- compute_next_sync floors to the minute.
    match = re.search(r"next in ~(\d+)m", r.text)
    assert match, r.text
    assert 45 <= int(match.group(1)) <= 50


def test_health_page_error_older_than_success_suppressed(tmp_path):
    cfg = _setup(tmp_path, "daily_time_accounting")
    now = datetime.now(timezone.utc)
    error_ts = (now - timedelta(hours=2)).isoformat()
    success_ts = (now - timedelta(minutes=5)).isoformat()

    (cfg.state_dir / "last_run.json").write_text(
        json.dumps({"daily_time_accounting": success_ts})
    )
    with (cfg.state_dir / "sync_errors.jsonl").open("w") as f:
        f.write(
            json.dumps(
                {
                    "ts": error_ts,
                    "tracker": "daily_time_accounting",
                    "error": "stale error: should not show",
                    "tb": "",
                }
            )
            + "\n"
        )

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/health")
    assert r.status_code == 200
    assert "stale error: should not show" not in r.text
    assert "health-row-error" not in r.text
    assert "health-ok" in r.text
