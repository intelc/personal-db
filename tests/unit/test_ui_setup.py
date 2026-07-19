"""Tests for the web setup wizard (/setup, /setup/{name})."""

from __future__ import annotations

import subprocess
import sys
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.services.daemon.http import build_app
from tests._daemon_auth import auth_headers


@pytest.fixture(autouse=True)
def _no_scheduler(monkeypatch):
    """Defense-in-depth against writing the GLOBAL launchd plist during tests.

    GET /setup/finish no longer installs anything on its own, but
    POST /setup/finish/install-daemon still would (it writes the plist at
    ~/Library/LaunchAgents/com.personal_db.daemon.plist regardless of
    cfg.root), so tests would otherwise risk clobbering the user's real
    daemon install. Individual tests that need to exercise the install path
    override this with monkeypatch.delenv.
    """
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


def test_setup_overview_lists_installed_and_bundled(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
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
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    # follow_redirects=False so we can assert the 303 → /setup/<name>
    r = client.post("/setup/install/habits", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/habits"
    assert (cfg.trackers_dir / "habits" / "manifest.yaml").exists()


def test_setup_tracker_get_renders_form(tmp_path):
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/habits")
    assert r.status_code == 200
    # 1 instructions step → ack checkbox with name _ack_0
    assert 'name="_ack_0"' in r.text
    assert "save &amp; test sync" in r.text  # submit button (HTML-escaped)


def test_setup_tracker_get_unknown_404(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/does_not_exist")
    assert r.status_code == 404


def test_setup_tracker_post_runs_steps_and_status(tmp_path):
    """Ack the instructions step → run_result.success, no failed steps."""
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/habits", data={"_ack_0": "1"})
    assert r.status_code == 200
    assert "✓ DONE" in r.text
    assert "test sync passed" in r.text


def test_setup_tracker_post_unacked_instruction_fails(tmp_path):
    """Submit without checking the ack box → step fails, no test sync."""
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/habits", data={})
    assert r.status_code == 200
    assert "FAILED" in r.text
    assert "not acknowledged" in r.text


def test_setup_tracker_post_persists_env_var(tmp_path):
    """env_var step writes to <root>/.env."""
    cfg = _init(tmp_path)
    _install(cfg.root, "github_commits")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
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
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/finish")
    assert r.status_code == 200
    assert "FINISH SETUP" in r.text
    assert "PERIODIC SYNC" in r.text
    assert "CONNECT AN AGENT" in r.text
    assert 'action="/setup/finish/install-daemon"' in r.text


def test_setup_finish_get_has_no_install_side_effect(tmp_path, monkeypatch):
    """GET /setup/finish must never call the daemon installer itself."""
    monkeypatch.delenv("PERSONAL_DB_NO_DAEMON", raising=False)
    calls = []
    monkeypatch.setattr(
        "personal_db.services.daemon.install.install", lambda root: calls.append(root)
    )

    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/finish")
    assert r.status_code == 200
    assert calls == []


def test_setup_daemon_install_post_redirects_with_flash(tmp_path, monkeypatch):
    monkeypatch.delenv("PERSONAL_DB_NO_DAEMON", raising=False)
    monkeypatch.setattr("sys.platform", "darwin")
    calls = []
    monkeypatch.setattr(
        "personal_db.services.daemon.install.install",
        lambda root: (calls.append(root), {"plist": "/fake/plist"})[1],
    )

    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/finish/install-daemon", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/setup/finish?daemon_ok=1")
    assert calls == [cfg.root]


def test_setup_daemon_install_post_skipped_when_disabled(tmp_path):
    """Relies on the autouse _no_scheduler fixture (PERSONAL_DB_NO_DAEMON=1)."""
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/finish/install-daemon", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "daemon_ok=1" in loc
    assert "skipped" in loc


def test_setup_finish_renders_daemon_flash(tmp_path):
    import urllib.parse

    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    msg = urllib.parse.quote("✓ daemon installed → /fake/plist")
    r = client.get(f"/setup/finish?daemon_ok=1&daemon_msg={msg}")
    assert r.status_code == 200
    assert "daemon installed" in r.text
    assert "setup-step-result" in r.text


def test_setup_finish_lists_all_mcp_targets(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/finish")
    assert "claude_code" in r.text
    assert "claude_desktop" in r.text
    assert "cursor" in r.text


def test_setup_mcp_install_unknown_404(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/mcp/install/not_a_target", follow_redirects=False)
    assert r.status_code == 404


def test_setup_mcp_install_redirects_with_flash(tmp_path, monkeypatch):
    """Mock the cursor target's auto-installer to return success; the route
    should redirect to /setup/finish?mcp=cursor&mcp_ok=1."""
    from personal_db.services.wizard import mcp_setup

    monkeypatch.setenv("PERSONAL_DB_ALLOW_GLOBAL_WRITES", "1")
    monkeypatch.setattr(
        mcp_setup._TARGETS["cursor"], "auto", lambda: (True, "wrote ~/.cursor/mcp.json")
    )
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/mcp/install/cursor", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/finish?mcp=cursor&mcp_ok=1"


def test_setup_mcp_install_blocked_on_scratch_root(tmp_path, monkeypatch):
    """Data root under tmp_path is a scratch root — the guard should refuse to
    call the target's auto-installer at all."""
    from personal_db.services.wizard import mcp_setup

    calls = []
    monkeypatch.setattr(mcp_setup._TARGETS["cursor"], "auto", lambda: (calls.append(1), (True, "x"))[1])
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/mcp/install/cursor", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "mcp_ok=0" in loc
    assert "mcp_msg=" in loc
    assert calls == []


def test_setup_daemon_install_blocked_on_scratch_root(tmp_path, monkeypatch):
    monkeypatch.delenv("PERSONAL_DB_NO_DAEMON", raising=False)
    monkeypatch.setattr("sys.platform", "darwin")
    # Defense-in-depth: even if the guard were broken, this test can't touch
    # the real ~/Library/LaunchAgents.
    monkeypatch.setattr(
        "personal_db.services.daemon.install._LAUNCHAGENTS_DIR", tmp_path / "launchagents"
    )

    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/finish/install-daemon", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "daemon_ok=0" in loc
    assert "temp" in urllib.parse.unquote(loc)


def test_setup_finish_renders_mcp_flash_with_msg(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    msg = urllib.parse.quote("blocked: scratch root")
    r = client.get(f"/setup/finish?mcp=cursor&mcp_ok=0&mcp_msg={msg}")
    assert r.status_code == 200
    assert "✗ failed for" in r.text
    assert "blocked: scratch root" in r.text


def test_setup_finish_renders_mcp_flash(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
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

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
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

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
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
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/oauth/no_such_tracker", data={"step_index": "0"})
    assert r.status_code == 404


def test_setup_oauth_whoop_redirects_to_provider_when_creds_set(tmp_path, monkeypatch):
    """Whoop is mechanically wired through the same generic browser flow as
    oura (StandardAdapter, no oauth_adapter.py) -- creds set -> 303 to
    Whoop's authorize endpoint with redirect_uri pinned to port 9876."""
    cfg = _init(tmp_path)
    _install(cfg.root, "whoop")
    monkeypatch.setenv("WHOOP_CLIENT_ID", "fake_id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "fake_secret")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    try:
        r = client.post(
            "/setup/oauth/whoop",
            data={"step_index": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("https://api.prod.whoop.com/oauth/oauth2/auth?")
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A9876%2Fcallback" in loc
        assert "client_id=fake_id" in loc
    finally:
        from personal_db.core.oauth import _shutdown_existing

        _shutdown_existing("whoop")


def test_setup_oauth_whoop_without_creds_redirects_back_with_message(tmp_path, monkeypatch):
    cfg = _init(tmp_path)
    _install(cfg.root, "whoop")
    monkeypatch.delenv("WHOOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("WHOOP_CLIENT_SECRET", raising=False)

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post(
        "/setup/oauth/whoop",
        data={"step_index": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/setup/whoop?msg=")
    assert "WHOOP_CLIENT_ID" in loc
    assert "WHOOP_CLIENT_SECRET" in loc


def test_setup_oauth_withings_redirects_to_provider_when_creds_set(tmp_path, monkeypatch):
    """Withings uses a custom oauth_adapter.py (WithingsAdapter) -- the
    installed tracker dir must carry that module along for
    ensure_adapter_from_manifest to load it, and the flow should still 303
    to the provider's authorize endpoint."""
    cfg = _init(tmp_path)
    _install(cfg.root, "withings")
    monkeypatch.setenv("WITHINGS_CLIENT_ID", "fake_id")
    monkeypatch.setenv("WITHINGS_CLIENT_SECRET", "fake_secret")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    try:
        r = client.post(
            "/setup/oauth/withings",
            data={"step_index": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("https://account.withings.com/oauth2_user/authorize2?")
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A9877%2Fcallback" in loc
        assert "client_id=fake_id" in loc
    finally:
        from personal_db.core.oauth import _shutdown_existing

        _shutdown_existing("withings")


def test_setup_oauth_withings_without_creds_redirects_back_with_message(tmp_path, monkeypatch):
    cfg = _init(tmp_path)
    _install(cfg.root, "withings")
    monkeypatch.delenv("WITHINGS_CLIENT_ID", raising=False)
    monkeypatch.delenv("WITHINGS_CLIENT_SECRET", raising=False)

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post(
        "/setup/oauth/withings",
        data={"step_index": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/setup/withings?msg=")
    assert "WITHINGS_CLIENT_ID" in loc
    assert "WITHINGS_CLIENT_SECRET" in loc


def test_setup_oauth_instagram_redirects_to_provider_when_creds_set(tmp_path, monkeypatch):
    """Instagram's OAuth step uses https + a self-signed localhost cert
    (InstagramAdapter, scheme=https). start_web_oauth spins up the local
    callback server -- including _get_ssl_context's openssl cert generation
    -- before issuing the 303, so this test exercises that path as a side
    effect. Skipped if openssl isn't on PATH."""
    import shutil

    if shutil.which("openssl") is None:
        pytest.skip("openssl not available on PATH")

    cfg = _init(tmp_path)
    _install(cfg.root, "instagram")
    monkeypatch.setenv("INSTAGRAM_APP_ID", "fake_id")
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", "fake_secret")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    try:
        r = client.post(
            "/setup/oauth/instagram",
            data={"step_index": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("https://www.instagram.com/oauth/authorize?")
        assert (
            "redirect_uri=https%3A%2F%2Flocalhost%3A9876%2Finstagram%2Fcallback"
            in loc
        )
        assert "client_id=fake_id" in loc
        cert = cfg.state_dir / "oauth" / ".ssl" / "localhost.crt"
        assert cert.exists()
    finally:
        from personal_db.core.oauth import _shutdown_existing

        _shutdown_existing("instagram")


def test_setup_oauth_instagram_without_creds_redirects_back_with_message(tmp_path, monkeypatch):
    cfg = _init(tmp_path)
    _install(cfg.root, "instagram")
    monkeypatch.delenv("INSTAGRAM_APP_ID", raising=False)
    monkeypatch.delenv("INSTAGRAM_APP_SECRET", raising=False)

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post(
        "/setup/oauth/instagram",
        data={"step_index": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/setup/instagram?msg=")
    assert "INSTAGRAM_APP_ID" in loc
    assert "INSTAGRAM_APP_SECRET" in loc


def test_setup_oauth_port_in_use_shows_friendly_message(tmp_path, monkeypatch):
    """If the callback port is already bound -- e.g. the user just ran
    another tracker's authorization and its local server hasn't shut down
    yet -- the redirect message should be actionable, not a raw
    "Address already in use" OSError string."""
    import socket

    cfg = _init(tmp_path)
    _install(cfg.root, "oura")
    monkeypatch.setenv("OURA_CLIENT_ID", "fake_id")
    monkeypatch.setenv("OURA_CLIENT_SECRET", "fake_secret")

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("localhost", 9877))
    blocker.listen(1)
    try:
        client = TestClient(build_app(cfg), headers=auth_headers(cfg))
        r = client.post(
            "/setup/oauth/oura",
            data={"step_index": "0"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("/setup/oura?msg=")
        msg = urllib.parse.unquote(loc.split("msg=", 1)[1])
        assert "Port 9877 is in use" in msg
        assert "wait for it to finish" in msg
    finally:
        blocker.close()


def test_setup_tracker_get_renders_authorize_button_for_oauth(tmp_path, monkeypatch):
    """When CLIENT_ID + CLIENT_SECRET are set, the OAuth step block on the
    per-tracker setup page renders an Authorize button targeting
    /setup/oauth/<name>."""
    cfg = _init(tmp_path)
    _install(cfg.root, "oura")
    monkeypatch.setenv("OURA_CLIENT_ID", "fake_id")
    monkeypatch.setenv("OURA_CLIENT_SECRET", "fake_secret")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/oura")
    assert r.status_code == 200
    assert 'formaction="/setup/oauth/oura"' in r.text
    assert "authorize via browser" in r.text


def test_setup_overview_marks_installed_with_icon(tmp_path):
    """Trackers without setup_steps render as ready (e.g. habits has 1
    instruction, but life_context with no steps is always ready). Habits has
    1 step, so before any successful test sync it needs attention."""
    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup")
    # The overview card shows a status chip reflecting the tracker's state.
    assert "Needs setup" in r.text or "Ready" in r.text


def test_recent_successful_sync_outranks_stale_needs_setup(tmp_path):
    """last_run.json is only written after a fully successful sync, so a
    tracker that synced recently must show Ready even if its wizard status
    icon still says needs-attention (e.g. set up outside the wizard)."""
    import json
    from datetime import UTC, datetime

    cfg = _init(tmp_path)
    _install(cfg.root, "habits")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    before = client.get("/setup")
    assert "Needs setup" in before.text  # habits: 1 step, never test-synced

    (cfg.state_dir / "last_run.json").write_text(
        json.dumps({"habits": datetime.now(UTC).isoformat()})
    )
    after = client.get("/setup")
    assert "● Ready" in after.text
    assert "Needs setup" not in after.text


@pytest.mark.darwin_only  # installs the darwin-gated code_agent_activity tracker
def test_install_hooks_step_renders_button(tmp_path):
    """Render a manifest with an install_hooks step; assert the button and
    onclick handler are present in the rendered HTML."""
    cfg = _init(tmp_path)
    _install(cfg.root, "code_agent_activity")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/code_agent_activity")
    assert r.status_code == 200
    assert "installHooks(this" in r.text
    assert "Install hooks" in r.text
    assert "action-output" in r.text


@pytest.mark.darwin_only  # installs the darwin-gated code_agent_activity tracker
def test_verify_hooks_step_renders_badge(tmp_path):
    """Render a manifest with a verify_hooks step; assert the status badge is present."""
    cfg = _init(tmp_path)
    _install(cfg.root, "code_agent_activity")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/code_agent_activity")
    assert r.status_code == 200
    assert "hook-status-badge" in r.text
    assert 'data-step-type="verify_hooks"' in r.text


@pytest.mark.darwin_only  # installs the darwin-gated code_agent_activity tracker
def test_note_step_renders_body(tmp_path):
    """Render a manifest with a note step; assert the note body text is present."""
    cfg = _init(tmp_path)
    _install(cfg.root, "code_agent_activity")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/code_agent_activity")
    assert r.status_code == 200
    assert "Codex CLI requires no setup" in r.text
    assert "~/.codex/sessions/" in r.text


def test_tracker_action_step_renders_button_and_status(tmp_path):
    """Plaid setup exposes Link/backup actions directly in the web setup UI."""
    cfg = _init(tmp_path)
    _install(cfg.root, "plaid")

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
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
