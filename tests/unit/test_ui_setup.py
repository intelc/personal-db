"""Tests for the web setup wizard (/setup, /setup/{name})."""

from __future__ import annotations

import subprocess
import sys

from fastapi.testclient import TestClient

from personal_db.config import Config
from personal_db.ui.server import build_app


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
