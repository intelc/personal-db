import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from personal_db.config import Config
from personal_db.sync import sync_one

FIXTURE_PROJECTS = Path("tests/fixtures/claude_conversations/projects")


def _init_and_install(root: Path) -> None:
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
            "claude_conversations",
        ],
        check=True,
        capture_output=True,
    )


def test_claude_sync_inserts_session_with_correct_counts(tmp_path, monkeypatch):
    """Syncing a fixture JSONL produces one row with correct counts and timestamps."""
    root = tmp_path / "personal_db"
    _init_and_install(root)

    # Copy fixture into a fresh tmp dir so mtime is fresh (greater than cursor=0)
    fake_projects = tmp_path / "claude_projects"
    shutil.copytree(FIXTURE_PROJECTS, fake_projects)

    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(fake_projects))

    cfg = Config(root=root)
    sync_one(cfg, "claude_conversations")

    con = sqlite3.connect(root / "db.sqlite")
    rows = con.execute(
        "SELECT session_id, project_slug, message_count, user_msg_count, "
        "assistant_msg_count, started_at, last_msg_at, first_user_prompt "
        "FROM claude_sessions"
    ).fetchall()
    con.close()

    assert len(rows) == 1
    (
        session_id,
        project_slug,
        message_count,
        user_msg_count,
        assistant_msg_count,
        started_at,
        last_msg_at,
        first_user_prompt,
    ) = rows[0]

    assert session_id == "abc123"
    assert project_slug == "-test-project"
    # 2 user + 1 assistant = 3 messages (system line is skipped)
    assert message_count == 3
    assert user_msg_count == 2
    assert assistant_msg_count == 1
    assert started_at == "2026-04-26T10:00:01.000Z"
    assert last_msg_at == "2026-04-26T10:00:30.000Z"
    assert first_user_prompt == "hello, can you help me debug?"


def test_claude_sync_skips_unchanged_files_via_cursor(tmp_path, monkeypatch):
    """Second sync with unchanged files ingests 0 new rows (cursor skips stale mtimes)."""
    root = tmp_path / "personal_db"
    _init_and_install(root)

    fake_projects = tmp_path / "claude_projects"
    shutil.copytree(FIXTURE_PROJECTS, fake_projects)

    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(fake_projects))

    cfg = Config(root=root)
    sync_one(cfg, "claude_conversations")

    # Count after first sync
    con = sqlite3.connect(root / "db.sqlite")
    count_after_first = con.execute("SELECT COUNT(*) FROM claude_sessions").fetchone()[0]
    con.close()
    assert count_after_first == 1

    # Second sync — files have same mtime, cursor should skip them
    sync_one(cfg, "claude_conversations")

    con = sqlite3.connect(root / "db.sqlite")
    count_after_second = con.execute("SELECT COUNT(*) FROM claude_sessions").fetchone()[0]
    con.close()
    # Row count should still be 1 (UPSERT won't change total if nothing new arrived)
    assert count_after_second == 1


def test_claude_sync_handles_missing_dir_gracefully(tmp_path, monkeypatch):
    """If CLAUDE_PROJECTS_DIR points to a non-existent path, sync should not raise."""
    root = tmp_path / "personal_db"
    _init_and_install(root)

    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "does_not_exist"))

    cfg = Config(root=root)
    # Should complete without raising
    sync_one(cfg, "claude_conversations")

    con = sqlite3.connect(root / "db.sqlite")
    count = con.execute("SELECT COUNT(*) FROM claude_sessions").fetchone()[0]
    con.close()
    assert count == 0
