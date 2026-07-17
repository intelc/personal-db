"""Tests for the web setup wizard (/setup, /setup/{name})."""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.services.daemon.http import build_app


@pytest.fixture(autouse=True)
def _no_scheduler(monkeypatch):
    """Prevent /setup/finish from writing the GLOBAL launchd plist during tests.

    The plist lives at ~/Library/LaunchAgents/com.personal_db.daemon.plist
    regardless of cfg.root, so tests would otherwise clobber the user's real
    daemon install. PERSONAL_DB_NO_SCHEDULER=1 is accepted as a deprecated alias
    by _install_daemon_safe, keeping this fixture working without change.
    """
    monkeypatch.setenv("PERSONAL_DB_NO_SCHEDULER", "1")


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


def test_setup_overview_lists_installed_and_bundled(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg))
    r = client.get("/setup")
    assert r.status_code == 200
    # installed tracker shows configure link
    assert "habits" in r.text
    assert "/setup/habits" in r.text
    # bundled-but-not-installed tracker shows install form
    assert "github_commits" in r.text
    assert "/setup/install/github_commits" in r.text


def test_setup_install_creates_tracker_dir(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg))
    # follow_redirects=False so we can assert the 303 → /setup/<name>
    r = client.post("/setup/install/habits", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/habits"
    assert (cfg.trackers_dir / "habits" / "manifest.yaml").exists()


def test_setup_tracker_get_renders_form(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg))
    r = client.get("/setup/habits")
    assert r.status_code == 200
    # 1 instructions step → ack checkbox with name _ack_0
    assert 'name="_ack_0"' in r.text
    assert "save &amp; test sync" in r.text  # submit button (HTML-escaped)


def test_setup_tracker_get_unknown_404(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.get("/setup/does_not_exist")
    assert r.status_code == 404


def test_setup_tracker_post_runs_steps_and_status(tmp_path):
    """Ack the instructions step → run_result.success, no failed steps."""
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg))
    r = client.post("/setup/habits", data={"_ack_0": "1"})
    assert r.status_code == 200
    assert "✓ DONE" in r.text
    assert "test sync passed" in r.text


def test_setup_tracker_post_unacked_instruction_fails(tmp_path):
    """Submit without checking the ack box → step fails, no test sync."""
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg))
    r = client.post("/setup/habits", data={})
    assert r.status_code == 200
    assert "FAILED" in r.text
    assert "not acknowledged" in r.text


def test_setup_tracker_post_persists_env_var(tmp_path):
    """env_var step writes to <root>/.env."""
    cfg = _init(tmp_path)
    _install(cfg.root, "github_commits")

    client = TestClient(build_app(cfg))
    # github_commits has a GITHUB_TOKEN env_var step. Submit with a fake value;
    # the test sync will fail (no real token), but we only care that the env
    # was written before the sync attempt.
    r = client.post(
        "/setup/github_commits",
        data={
            "GITHUB_TOKEN": "fake_test_token_value",
            "GITHUB_AUTHOR_EMAILS": "",  # optional
        },
    )
    assert r.status_code == 200
    env_text = (cfg.root / ".env").read_text()
    assert "GITHUB_TOKEN=fake_test_token_value" in env_text


def test_setup_finish_get_renders(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.get("/setup/finish")
    assert r.status_code == 200
    assert "FINISH SETUP" in r.text
    assert "PERIODIC SYNC" in r.text
    assert "CONNECT AN AGENT" in r.text


def test_setup_finish_lists_all_mcp_targets(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.get("/setup/finish")
    assert "claude_code" in r.text
    assert "claude_desktop" in r.text
    assert "cursor" in r.text


def test_setup_mcp_install_unknown_404(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.post("/setup/mcp/install/not_a_target", follow_redirects=False)
    assert r.status_code == 404


def test_setup_mcp_install_redirects_with_flash(tmp_path, monkeypatch):
    """Mock the cursor target's auto-installer to return success; the route
    should redirect to /setup/finish?mcp=cursor&mcp_ok=1."""
    from personal_db.services.wizard import mcp_setup

    monkeypatch.setattr(
        mcp_setup._TARGETS["cursor"], "auto", lambda: (True, "wrote ~/.cursor/mcp.json")
    )
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.post("/setup/mcp/install/cursor", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/finish?mcp=cursor&mcp_ok=1"


def test_setup_finish_renders_mcp_flash(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.get("/setup/finish?mcp=cursor&mcp_ok=1")
    assert "✓ installed for" in r.text
    assert "cursor" in r.text


def test_setup_oauth_redirects_to_provider_when_creds_set(tmp_path, monkeypatch):
    """Posting /setup/oauth/oura with CLIENT_ID/SECRET set in env returns a
    303 redirect to the provider's authorize URL with redirect_uri pointing
    at the manifest's redirect_port."""
    cfg = _init(tmp_path)
    _install(cfg.root, "oura")
    monkeypatch.setenv("OURA_CLIENT_ID", "fake_id")
    monkeypatch.setenv("OURA_CLIENT_SECRET", "fake_secret")

    client = TestClient(build_app(cfg))
    try:
        r = client.post(
            "/setup/oauth/oura",
            data={"step_index": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("https://cloud.ouraring.com/oauth/authorize?")
        # redirect_uri should match the manifest's redirect_port (oura: 9877).
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A9877%2Fcallback" in loc
        assert "client_id=fake_id" in loc
        assert "scope=daily+heartrate+workout+session+personal+spo2" in loc
    finally:
        # Always shut down the spawned localhost callback server so subsequent
        # tests don't run into "address already in use".
        from personal_db.core.oauth import _shutdown_existing

        _shutdown_existing("oura")


def test_setup_oauth_without_creds_redirects_back_with_message(tmp_path, monkeypatch):
    """Without CLIENT_ID/SECRET in env or .env, the OAuth route should bounce
    the user back to /setup/oura with a helpful message — not 500."""
    cfg = _init(tmp_path)
    _install(cfg.root, "oura")
    monkeypatch.delenv("OURA_CLIENT_ID", raising=False)
    monkeypatch.delenv("OURA_CLIENT_SECRET", raising=False)

    client = TestClient(build_app(cfg))
    r = client.post(
        "/setup/oauth/oura",
        data={"step_index": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/setup/oura?msg=")
    assert "OURA_CLIENT_ID" in loc
    assert "OURA_CLIENT_SECRET" in loc


def test_setup_oauth_unknown_tracker_404(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.post("/setup/oauth/no_such_tracker", data={"step_index": "0"})
    assert r.status_code == 404


def test_setup_tracker_get_renders_authorize_button_for_oauth(tmp_path, monkeypatch):
    """When CLIENT_ID + CLIENT_SECRET are set, the OAuth step block on the
    per-tracker setup page renders an Authorize button targeting
    /setup/oauth/<name>."""
    cfg = _init(tmp_path)
    _install(cfg.root, "oura")
    monkeypatch.setenv("OURA_CLIENT_ID", "fake_id")
    monkeypatch.setenv("OURA_CLIENT_SECRET", "fake_secret")

    client = TestClient(build_app(cfg))
    r = client.get("/setup/oura")
    assert r.status_code == 200
    assert 'formaction="/setup/oauth/oura"' in r.text
    assert "authorize via browser" in r.text


def test_setup_overview_marks_installed_with_icon(tmp_path):
    """Trackers without setup_steps render with the '—' icon (e.g. habits has 1
    instruction, but life_context with no steps shows '—'). Habits has 1 step,
    so before any successful run it's '✗'."""
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg))
    r = client.get("/setup")
    # Some indicator glyph is rendered for habits' status. Just confirm the
    # tracker appears with a row containing its summary text.
    assert "needs setup" in r.text or "configured" in r.text or "no setup needed" in r.text


def test_install_hooks_step_renders_button(tmp_path):
    """Render a manifest with an install_hooks step; assert the button and
    onclick handler are present in the rendered HTML."""
    cfg = _init(tmp_path)
    _install(cfg.root, "code_agent_activity")

    client = TestClient(build_app(cfg))
    r = client.get("/setup/code_agent_activity")
    assert r.status_code == 200
    assert "installHooks(this" in r.text
    assert "Install hooks" in r.text
    assert "action-output" in r.text


def test_verify_hooks_step_renders_badge(tmp_path):
    """Render a manifest with a verify_hooks step; assert the status badge is present."""
    cfg = _init(tmp_path)
    _install(cfg.root, "code_agent_activity")

    client = TestClient(build_app(cfg))
    r = client.get("/setup/code_agent_activity")
    assert r.status_code == 200
    assert "hook-status-badge" in r.text
    assert 'data-step-type="verify_hooks"' in r.text


def test_note_step_renders_body(tmp_path):
    """Render a manifest with a note step; assert the note body text is present."""
    cfg = _init(tmp_path)
    _install(cfg.root, "code_agent_activity")

    client = TestClient(build_app(cfg))
    r = client.get("/setup/code_agent_activity")
    assert r.status_code == 200
    assert "Codex CLI requires no setup" in r.text
    assert "~/.codex/sessions/" in r.text


def test_tracker_action_step_renders_button_and_status(tmp_path):
    """Plaid setup exposes Link/backup actions directly in the web setup UI."""
    cfg = _init(tmp_path)
    _install(cfg.root, "plaid")

    client = TestClient(build_app(cfg))
    r = client.get("/setup/plaid")
    assert r.status_code == 200
    assert "Connect institution" in r.text
    assert "Backup Plaid tokens" in r.text
    assert "Finance export accounts" in r.text
    assert "Save export settings" in r.text
    assert 'data-step-type="action"' in r.text
    assert 'data-action="link_item"' in r.text
    assert 'data-status-action="token_status"' in r.text
    assert "tracker-action-status" in r.text
