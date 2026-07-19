"""Tests for GET/PUT /api/v1/dashboard and the dashboard page's edit affordance.

Backs roadmap item 10 ("Dashboard editing in the UI") -- see
services/daemon/routes/dashboard.py, services/ui/viz.py's
load_dashboard_slugs/save_dashboard_slugs, and dashboard.html/pdb-dashboard.js.
"""

import yaml
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.services.daemon.http import build_app
from tests._daemon_auth import auth_headers
from tests._validation_helpers import mark_valid


def _make_tracker_with_viz(tmp_root, name, short_slugs):
    """Minimal runnable tracker exposing one non-auto viz per entry in
    `short_slugs`, named/described from the slug itself so assertions can
    reference them predictably."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump({
            "name": name,
            "description": name,
            "permission_type": "none",
            "setup_steps": [],
            "schedule": {"every": "1h"},
            "time_column": "ts",
            "granularity": "event",
            "schema": {"tables": {name: {"columns": {
                "id": {"type": "TEXT", "semantic": "id"},
                "ts": {"type": "TEXT", "semantic": "ts"},
            }}}},
        })
    )
    (d / "schema.sql").write_text(
        f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, ts TEXT);"
    )
    (d / "ingest.py").write_text(
        "def backfill(t, start, end):\n    pass\ndef sync(t):\n    pass\n"
    )
    viz_src = ["def list_visualizations():", "    return ["]
    for short in short_slugs:
        viz_src.append(
            f"        {{'slug': {short!r}, 'name': {short!r}, "
            f"'description': 'desc for {short}', 'render': lambda cfg: '<p>{short}</p>'}},"
        )
    viz_src.append("    ]")
    (d / "visualizations.py").write_text("\n".join(viz_src) + "\n")
    mark_valid(cfg, name)
    return cfg


def test_dashboard_get_default_shape_is_all_enabled(tmp_root):
    """No dashboard.yaml -> every non-auto viz is enabled, in registry order."""
    cfg = _make_tracker_with_viz(tmp_root, "alpha", ["one", "two"])
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/dashboard")
    assert r.status_code == 200
    body = r.json()
    by_slug = {v["slug"]: v for v in body["viz"]}
    assert "_builtin:health" in by_slug
    assert "alpha:one" in by_slug
    assert "alpha:two" in by_slug
    for entry in body["viz"]:
        assert entry["enabled"] is True
        assert entry["order"] is not None
        assert entry["tracker"] in ("_builtin", "alpha")
    # order values are a contiguous 0..n-1 permutation
    orders = sorted(v["order"] for v in body["viz"])
    assert orders == list(range(len(body["viz"])))
    one = by_slug["alpha:one"]
    assert one["name"] == "one"
    assert one["description"] == "desc for one"


def test_dashboard_get_reflects_configured_enabled_and_order(tmp_root):
    cfg = _make_tracker_with_viz(tmp_root, "alpha", ["one", "two"])
    config_dir = cfg.root / ".config"
    config_dir.mkdir()
    (config_dir / "dashboard.yaml").write_text(
        yaml.safe_dump({"viz": ["alpha:two", "_builtin:health"]})
    )
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/dashboard")
    assert r.status_code == 200
    by_slug = {v["slug"]: v for v in r.json()["viz"]}
    assert by_slug["alpha:two"]["enabled"] is True
    assert by_slug["alpha:two"]["order"] == 0
    assert by_slug["_builtin:health"]["enabled"] is True
    assert by_slug["_builtin:health"]["order"] == 1
    assert by_slug["alpha:one"]["enabled"] is False
    assert by_slug["alpha:one"]["order"] is None


def test_dashboard_put_writes_yaml_and_next_get_reflects_it(tmp_root):
    cfg = _make_tracker_with_viz(tmp_root, "alpha", ["one", "two"])
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.put("/api/v1/dashboard", json={"viz": ["alpha:two", "alpha:one"]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "viz": ["alpha:two", "alpha:one"]}

    written = yaml.safe_load((cfg.root / ".config" / "dashboard.yaml").read_text())
    assert written == {"viz": ["alpha:two", "alpha:one"]}

    r = client.get("/api/v1/dashboard")
    by_slug = {v["slug"]: v for v in r.json()["viz"]}
    assert by_slug["alpha:two"]["order"] == 0
    assert by_slug["alpha:one"]["order"] == 1
    assert by_slug["_builtin:health"]["enabled"] is False


def test_dashboard_put_rejects_unknown_slug(tmp_root):
    cfg = _make_tracker_with_viz(tmp_root, "alpha", ["one"])
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.put("/api/v1/dashboard", json={"viz": ["alpha:one", "nope:bogus"]})
    assert r.status_code == 400
    assert "nope:bogus" in r.json()["detail"]
    # Nothing written on a rejected PUT.
    assert not (cfg.root / ".config" / "dashboard.yaml").exists()


def test_dashboard_put_rejects_non_list_body(tmp_root):
    cfg = _make_tracker_with_viz(tmp_root, "alpha", ["one"])
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.put("/api/v1/dashboard", json={"viz": "alpha:one"})
    assert r.status_code == 400


def test_dashboard_put_rejects_cross_origin_write(tmp_root):
    cfg = _make_tracker_with_viz(tmp_root, "alpha", ["one"])
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.put(
        "/api/v1/dashboard",
        json={"viz": ["alpha:one"]},
        headers={"origin": "http://attacker.example"},
    )
    assert r.status_code == 403


def test_dashboard_page_contains_edit_affordance(tmp_root):
    cfg = _make_tracker_with_viz(tmp_root, "alpha", ["one"])
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="pdb-dash-edit-toggle"' in r.text
    assert 'id="pdb-dash-edit-panel"' in r.text
    assert "Edit dashboard" in r.text
    # The old CONFIGURE <details> leftover is gone.
    assert "CONFIGURE" not in r.text


def test_dashboard_page_renders_disabled_group_when_config_excludes_viz(tmp_root):
    """Regression test: rendering a disabled tracker group used to blow up
    with `TypeError: 'builtin_function_or_method' object is not iterable`
    because the group dict's `items` key shadowed dict.items() under Jinja's
    attribute lookup (`group.items` resolved to the bound method, not the
    list). The panel's grouped-viz template branch only runs when at least
    one viz is disabled, so this needs an explicit dashboard.yaml."""
    cfg = _make_tracker_with_viz(tmp_root, "alpha", ["one", "two"])
    config_dir = cfg.root / ".config"
    config_dir.mkdir()
    (config_dir / "dashboard.yaml").write_text(yaml.safe_dump({"viz": ["alpha:one"]}))
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "dash-edit-group-head" in r.text
    assert "ALPHA" in r.text  # humanized tracker-group heading
    assert "two" in r.text  # the disabled viz's name still renders


def test_dashboard_edit_affordance_absent_on_welcome_hero(tmp_root):
    """Zero-tracker root shows the welcome hero, not the page-head/edit button."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "Connect your first source" in r.text
    assert 'id="pdb-dash-edit-toggle"' not in r.text
