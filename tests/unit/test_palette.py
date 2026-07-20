"""Tests for the Cmd+K / Ctrl+K navigation palette asset wiring.

The palette's index is built client-side from the sidebar DOM (see
pdb-palette.js), so there's no server-side data contract to test here --
just that the page pulls in the script, matching the asset-assertion
pattern used by test_ui_viz.py::test_base_uses_vendored_ag_assets.
"""

import subprocess
import sys

from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.services.daemon.http import build_app
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


def test_base_includes_palette_script(tmp_path):
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")

    assert r.status_code == 200
    assert "/static/pdb-palette.js?v=1" in r.text


def test_palette_script_included_on_other_pages_too(tmp_path):
    # base.html is shared chrome -- the include should show up on any page
    # that extends it, not just the dashboard.
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/health")

    assert r.status_code == 200
    assert "/static/pdb-palette.js?v=1" in r.text
