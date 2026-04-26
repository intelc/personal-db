import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from personal_db.config import Config
from personal_db.sync import sync_one


def _init_and_install(root):
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "install",
            "github_commits",
        ],
        check=True,
        capture_output=True,
    )


def _make_response(body, status_code=200, link=""):
    m = MagicMock()
    m.status_code = status_code
    m.headers = {"Link": link} if link else {}
    m.json.return_value = body
    m.raise_for_status = MagicMock()
    return m


def _make_fake_get(repos, commits1, commits2):
    def fake_get(url, **kwargs):
        if "/user/repos" in url:
            return _make_response(repos)
        elif "/repos/intelc/repo1/commits" in url:
            return _make_response(commits1)
        elif "/repos/intelc/repo2/commits" in url:
            return _make_response(commits2)
        else:
            raise AssertionError(f"unexpected URL: {url}")

    return fake_get


def test_github_sync_inserts_commits_from_active_repos(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    _init_and_install(root)
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("GITHUB_USER", "intel")

    repos = json.loads(Path("tests/fixtures/github/repos.json").read_text())
    commits1 = json.loads(Path("tests/fixtures/github/commits_repo1.json").read_text())
    commits2 = json.loads(Path("tests/fixtures/github/commits_repo2.json").read_text())

    cfg = Config(root=root)
    with patch("requests.get", side_effect=_make_fake_get(repos, commits1, commits2)):
        sync_one(cfg, "github_commits")

    rows = (
        sqlite3.connect(root / "db.sqlite")
        .execute("SELECT sha, repo, committed_at FROM github_commits ORDER BY committed_at DESC")
        .fetchall()
    )
    assert len(rows) == 3
    # Newest first
    assert rows[0][0] == "aaaa1111"
    # Both repos populated
    assert {r[1] for r in rows} == {"intelc/repo1", "intelc/repo2"}
    # UTC normalization: stored with explicit +00:00 offset
    assert all(r[2].endswith("+00:00") for r in rows)


def test_github_sync_skips_repos_pushed_before_cursor(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    _init_and_install(root)
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("GITHUB_USER", "intel")

    repos = json.loads(Path("tests/fixtures/github/repos.json").read_text())
    commits1 = json.loads(Path("tests/fixtures/github/commits_repo1.json").read_text())
    commits2 = json.loads(Path("tests/fixtures/github/commits_repo2.json").read_text())

    cfg = Config(root=root)

    # Set cursor to 2026-04-22: repo1 (pushed 04-25) should be scanned,
    # repo2 (pushed 04-20) should be skipped because pushed_at <= cursor.
    cursor_value = "2026-04-22T00:00:00+00:00"

    repo2_fetched = []

    def fake_get(url, **kwargs):
        if "/user/repos" in url:
            return _make_response(repos)
        elif "/repos/intelc/repo1/commits" in url:
            return _make_response(commits1)
        elif "/repos/intelc/repo2/commits" in url:
            repo2_fetched.append(url)
            return _make_response(commits2)
        else:
            raise AssertionError(f"unexpected URL: {url}")

    # Pre-seed the cursor before syncing
    from personal_db.tracker import Tracker

    Tracker(name="github_commits", cfg=cfg, manifest=None).cursor.set(cursor_value)

    with patch("requests.get", side_effect=fake_get):
        sync_one(cfg, "github_commits")

    # repo2 should never have been fetched (pushed_at 04-20 <= cursor 04-22)
    assert repo2_fetched == [], "repo2 should have been skipped due to cursor"

    rows = (
        sqlite3.connect(root / "db.sqlite")
        .execute("SELECT sha, repo FROM github_commits")
        .fetchall()
    )
    # Only repo1 commits (2 rows)
    assert len(rows) == 2
    assert all(r[1] == "intelc/repo1" for r in rows)
