"""Tests for the "Add your own source" scaffold flow (/setup/new)."""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.core.manifest import load_manifest
from personal_db.services.daemon.http import build_app
from tests._daemon_auth import auth_headers


@pytest.fixture(autouse=True)
def _no_scheduler(monkeypatch):
    # See tests/unit/test_ui_setup.py's identical fixture for why this exists.
    monkeypatch.setenv("PERSONAL_DB_NO_DAEMON", "1")


def _init(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    return Config(root=root)


def test_setup_new_get_renders_form(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup/new")
    assert r.status_code == 200
    assert 'action="/setup/new"' in r.text
    assert 'name="slug"' in r.text
    assert 'name="title"' in r.text
    assert 'name="description"' in r.text
    assert "creating-trackers.md" in r.text


def test_setup_new_post_creates_tracker_files(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post(
        "/setup/new",
        data={"slug": "my_journal", "title": "My Journal", "description": "Daily log entries"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/setup/my_journal?created=1"

    dest = cfg.trackers_dir / "my_journal"
    assert (dest / "manifest.yaml").exists()
    assert (dest / "schema.sql").exists()
    assert (dest / "ingest.py").exists()

    manifest = load_manifest(dest / "manifest.yaml")
    assert manifest.name == "my_journal"
    assert manifest.title == "My Journal"
    assert manifest.description == "Daily log entries"
    assert manifest.display_title() == "My Journal"


def test_setup_new_post_without_title_or_description(tmp_path):
    """Optional fields stay unset; scaffold's TODO description survives."""
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/new", data={"slug": "bare_tracker"}, follow_redirects=False)
    assert r.status_code == 303

    dest = cfg.trackers_dir / "bare_tracker"
    manifest = load_manifest(dest / "manifest.yaml")
    assert manifest.title is None
    assert manifest.description == "TODO describe what this tracker captures"
    # Falls back to the humanized slug when no title was given.
    assert manifest.display_title() == "Bare Tracker"


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "a",  # too short
        "1abc",  # must start with a letter
        "My_Tracker",  # uppercase not allowed
        "has space",
        "has-dash",
        "a" * 33,  # too long
    ],
)
def test_setup_new_post_rejects_bad_slug(tmp_path, slug):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/new", data={"slug": slug}, follow_redirects=False)
    assert r.status_code == 200  # re-renders the form, no redirect
    assert "Slug must start with a lowercase letter" in r.text
    if slug:
        assert not (cfg.trackers_dir / slug).exists()


def test_setup_new_post_rejects_bundled_collision(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/setup/new", data={"slug": "habits"}, follow_redirects=False)
    assert r.status_code == 200
    assert "collides with a bundled tracker name" in r.text
    assert not (cfg.trackers_dir / "habits").exists()


def test_setup_new_post_rejects_installed_collision(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    first = client.post("/setup/new", data={"slug": "my_journal"}, follow_redirects=False)
    assert first.status_code == 303

    second = client.post("/setup/new", data={"slug": "my_journal"}, follow_redirects=False)
    assert second.status_code == 200
    assert "already installed" in second.text
    # Original files untouched.
    manifest = load_manifest(cfg.trackers_dir / "my_journal" / "manifest.yaml")
    assert manifest.name == "my_journal"


def test_setup_new_post_form_values_preserved_on_error(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post(
        "/setup/new",
        data={"slug": "1bad", "title": "Keep Me", "description": "Keep this too"},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Keep Me" in r.text
    assert "Keep this too" in r.text


def test_created_tracker_appears_on_setup_overview(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    client.post("/setup/new", data={"slug": "my_journal", "title": "My Journal"})

    r = client.get("/setup")
    assert r.status_code == 200
    assert "My Journal" in r.text
    assert "/setup/my_journal" in r.text


def test_setup_new_entry_card_on_overview(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/setup")
    assert r.status_code == 200
    assert 'href="/setup/new"' in r.text
    assert "Add your own source" in r.text


def test_created_notice_renders_on_query_param(tmp_path):
    cfg = _init(tmp_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    client.post("/setup/new", data={"slug": "my_journal", "title": "My Journal"})

    r = client.get("/setup/my_journal?created=1")
    assert r.status_code == 200
    assert "Scaffolded" in r.text
    assert str(cfg.trackers_dir / "my_journal") in r.text
    assert "manifest.yaml" in r.text
    assert "ingest.py" in r.text
    assert "creating-trackers.md" in r.text
    assert "copy" in r.text.lower()

    # Without ?created=1 the notice is absent.
    r2 = client.get("/setup/my_journal")
    assert r2.status_code == 200
    assert "Scaffolded" not in r2.text
