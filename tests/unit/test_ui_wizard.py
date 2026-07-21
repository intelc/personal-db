"""Tests for the step-per-page first-run setup wizard
(/setup/{name}/wizard, /setup/{name}/wizard/{i}, /setup/{name}/wizard/finish).

Fixture pattern mirrors test_ui_setup.py: a real `personal-db init` +
`tracker install` via subprocess (so the installed copy is genuine), then a
FastAPI TestClient built from the same Config.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
import yaml
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.services.daemon.http import build_app
from tests._daemon_auth import auth_headers


@pytest.fixture(autouse=True)
def _no_scheduler(monkeypatch):
    """See test_ui_setup.py's identical fixture -- keeps the daemon installer
    from touching the real launchd plist during tests."""
    monkeypatch.setenv("PERSONAL_DB_NO_DAEMON", "1")


def _init(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    return Config(root=root)


def _install(root, name):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "install",
            name,
        ],
        check=True,
        capture_output=True,
    )


def _clear_setup_steps(cfg: Config, name: str) -> None:
    """Rewrite an installed tracker's manifest.yaml with an empty
    setup_steps list -- used for the "no setup needed" wizard path. No
    bundled tracker ships with zero setup steps, so this is synthesized."""
    p = cfg.trackers_dir / name / "manifest.yaml"
    data = yaml.safe_load(p.read_text())
    data["setup_steps"] = []
    p.write_text(yaml.safe_dump(data, sort_keys=False))


def test_wizard_root_redirects_to_step_1(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/habits/wizard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/habits/wizard/1"


def test_wizard_root_unknown_tracker_404(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/does_not_exist/wizard", follow_redirects=False)
    assert r.status_code == 404


def test_wizard_root_empty_setup_steps_goes_to_finish(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")
    _clear_setup_steps(cfg, "habits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/habits/wizard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/habits/wizard/finish"


def test_wizard_step_page_renders_right_step_and_progress(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "github_commits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    # github_commits: [0] instructions, [1] GITHUB_TOKEN, [2] GITHUB_AUTHOR_EMAILS
    r = client.get("/setup/github_commits/wizard/2")
    assert r.status_code == 200
    assert "Step 2 of 3" in r.text
    assert 'name="GITHUB_TOKEN"' in r.text
    # not step 1's or step 3's field
    assert 'name="GITHUB_AUTHOR_EMAILS"' not in r.text


def test_wizard_out_of_range_step_redirects(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/habits/wizard/99", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/habits/wizard"


def test_wizard_post_valid_env_var_advances_to_next_step(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "github_commits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post(
        "/setup/github_commits/wizard/2",
        data={"GITHUB_TOKEN": "fake_test_token_value"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/github_commits/wizard/3"
    env_text = (cfg.root / ".env").read_text()
    assert "GITHUB_TOKEN=fake_test_token_value" in env_text


def test_wizard_post_missing_required_env_var_rerenders_with_failure(tmp_path, monkeypatch):
    # Other tests in this module set GITHUB_TOKEN in the real process
    # os.environ (that's how _process_step persists env vars) -- since
    # pytest runs this module's tests in one process, guard against that
    # leaking in here and masking the "missing" case.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cfg = _init(tmp_path)
    _install(cfg.root, "github_commits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/github_commits/wizard/2", data={}, follow_redirects=False)
    assert r.status_code == 200  # re-rendered, not redirected
    assert "GITHUB_TOKEN required" in r.text
    assert "Step 2 of 3" in r.text


def test_wizard_post_last_step_redirects_to_finish(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")  # 1 setup step: instructions
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/habits/wizard/1", data={"_ack_0": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/habits/wizard/finish"


def test_wizard_unchecked_instructions_ack_does_not_advance(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/habits/wizard/1", data={}, follow_redirects=False)
    assert r.status_code == 200  # re-rendered, not redirected
    assert "Please confirm you&#39;ve completed these steps." in r.text or (
        "Please confirm you" in r.text and "completed these steps" in r.text
    )


def test_wizard_oauth_step_without_token_does_not_advance(tmp_path, monkeypatch):
    """oura's setup_steps: [0] instructions, [1] OURA_CLIENT_ID,
    [2] OURA_CLIENT_SECRET, [3] oauth. No token file on disk yet -- Continue
    must not silently pass this step ("skipped" would be a dead end)."""
    cfg = _init(tmp_path)
    _install(cfg.root, "oura")
    monkeypatch.setenv("OURA_CLIENT_ID", "fake_id")
    monkeypatch.setenv("OURA_CLIENT_SECRET", "fake_secret")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/oura/wizard/4", data={}, follow_redirects=False)
    assert r.status_code == 200  # re-rendered, not redirected to finish
    assert "Click Authorize" in r.text
    assert "Step 4 of 4" in r.text


def test_wizard_finish_get_renders_run_first_sync(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/habits/wizard/finish")
    assert r.status_code == 200
    assert "Run first sync" in r.text


def test_wizard_finish_post_success_renders_connected(tmp_path):
    """habits' ingest.py sync()/backfill() are no-ops, so this exercises the
    real run_first_sync() path end-to-end without needing to fake sync_one /
    backfill_mod.start_async (test_ui_setup.py's existing habits-based tests
    take the same real-sync approach rather than mocking)."""
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/habits/wizard/finish")
    assert r.status_code == 200
    assert "connected" in r.text
    assert "test sync passed" in r.text
    assert 'href="/t/habits"' in r.text
    assert 'href="/setup"' in r.text


def test_install_redirects_to_wizard_for_fresh_install(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/install/habits", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/habits/wizard"


def test_install_already_installed_redirects_to_settings_not_wizard(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/install/habits", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/habits"


def test_settings_page_shows_setup_banner_when_never_configured(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")  # installed but never test-synced -- icon '✗'
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/habits")
    assert r.status_code == 200
    assert "isn&#39;t set up yet" in r.text or "isn't set up yet" in r.text
    assert 'href="/setup/habits/wizard"' in r.text


def test_settings_page_hides_setup_banner_once_configured(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    client.post("/setup/habits", data={"_ack_0": "1"})  # real settings-page submit
    r = client.get("/setup/habits")
    assert r.status_code == 200
    assert "isn't set up yet" not in r.text


def test_wizard_breadcrumb_shows_settings_tracker_setup(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/habits/wizard/1")
    assert r.status_code == 200
    assert "Settings" in r.text
    assert "Setup" in r.text
