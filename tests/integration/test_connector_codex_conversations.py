import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from personal_db.config import Config
from personal_db.sync import sync_one

FIXTURE_SESSIONS = Path("tests/fixtures/codex_conversations/sessions")


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
            "codex_conversations",
        ],
        check=True,
        capture_output=True,
    )


def test_codex_sync_inserts_session_with_correct_counts(tmp_path, monkeypatch):
    """Syncing a fixture JSONL produces one row with correct counts and timestamps."""
    root = tmp_path / "personal_db"
    _init_and_install(root)

    fake_sessions = tmp_path / "codex_sessions"
    shutil.copytree(FIXTURE_SESSIONS, fake_sessions)

    monkeypatch.setenv("CODEX_SESSIONS_DIR", str(fake_sessions))

    cfg = Config(root=root)
    sync_one(cfg, "codex_conversations")

    con = sqlite3.connect(root / "db.sqlite")
    rows = con.execute(
        "SELECT session_id, cwd, started_at, last_event_at, event_count, "
        "user_msg_count, assistant_msg_count, first_user_prompt "
        "FROM codex_sessions"
    ).fetchall()
    con.close()

    assert len(rows) == 1
    (
        session_id,
        cwd,
        started_at,
        last_event_at,
        event_count,
        user_msg_count,
        assistant_msg_count,
        first_user_prompt,
    ) = rows[0]

    assert session_id == "550e8400-e29b-41d4-a716-446655440000"
    assert cwd == "/Users/yihengchen/codestuff/aiexperiments/personal_db"
    assert started_at == "2026-04-26T10:00:00.000Z"
    # last_event_at should be the latest timestamp across all lines
    assert last_event_at == "2026-04-26T10:00:06.000Z"
    assert event_count == 2  # 1 user + 1 assistant response_item
    assert user_msg_count == 1
    assert assistant_msg_count == 1
    assert first_user_prompt == "Write a hello world in Python"


def test_codex_sync_skips_unchanged_files_via_cursor(tmp_path, monkeypatch):
    """Second sync with unchanged files ingests 0 new rows (cursor skips stale mtimes)."""
    root = tmp_path / "personal_db"
    _init_and_install(root)

    fake_sessions = tmp_path / "codex_sessions"
    shutil.copytree(FIXTURE_SESSIONS, fake_sessions)

    monkeypatch.setenv("CODEX_SESSIONS_DIR", str(fake_sessions))

    cfg = Config(root=root)
    sync_one(cfg, "codex_conversations")

    con = sqlite3.connect(root / "db.sqlite")
    count_after_first = con.execute("SELECT COUNT(*) FROM codex_sessions").fetchone()[0]
    con.close()
    assert count_after_first == 1

    # Second sync — files have same mtime, cursor should skip them
    sync_one(cfg, "codex_conversations")

    con = sqlite3.connect(root / "db.sqlite")
    count_after_second = con.execute("SELECT COUNT(*) FROM codex_sessions").fetchone()[0]
    con.close()
    assert count_after_second == 1


def test_codex_sync_handles_missing_dir_gracefully(tmp_path, monkeypatch):
    """If CODEX_SESSIONS_DIR points to a non-existent path, sync should not raise."""
    root = tmp_path / "personal_db"
    _init_and_install(root)

    monkeypatch.setenv("CODEX_SESSIONS_DIR", str(tmp_path / "does_not_exist"))

    cfg = Config(root=root)
    # Should complete without raising
    sync_one(cfg, "codex_conversations")

    con = sqlite3.connect(root / "db.sqlite")
    count = con.execute("SELECT COUNT(*) FROM codex_sessions").fetchone()[0]
    con.close()
    assert count == 0
