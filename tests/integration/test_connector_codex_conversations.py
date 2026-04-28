import json
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
    assert cwd == "/Users/test/code/example"
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


def test_codex_sync_uses_history_jsonl_for_first_user_prompt(tmp_path, monkeypatch):
    """history.jsonl takes precedence over the session file for first_user_prompt."""
    root = tmp_path / "personal_db"
    _init_and_install(root)

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    history_path = tmp_path / "history.jsonl"

    sid = "019dcb33-aaaa-bbbb-cccc-ddddeeee0001"

    # Session file with AGENTS.md injection as the only user message — fallback
    # would return None (synthetic skipped); history.jsonl provides the real prompt.
    session_meta_payload = json.dumps({"id": sid, "timestamp": "2026-04-26T10:00:00Z"})
    response_item_payload = json.dumps(
        {
            "type": "message",
            "role": "user",
            "content": "[{'type': 'input_text', 'text': '# AGENTS.md instructions for /tmp'}]",
        }
    )
    rollout = sessions_dir / "rollout-test.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-26T10:00:00Z",
                "type": "session_meta",
                "payload": session_meta_payload,
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-04-26T10:00:01Z",
                "type": "response_item",
                "payload": response_item_payload,
            }
        )
        + "\n"
    )

    history_path.write_text(
        json.dumps({"session_id": sid, "ts": 1745604000, "text": "real first user prompt"}) + "\n"
    )

    monkeypatch.setenv("CODEX_SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("CODEX_HISTORY_FILE", str(history_path))

    cfg = Config(root=root)
    sync_one(cfg, "codex_conversations")

    con = sqlite3.connect(root / "db.sqlite")
    rows = con.execute("SELECT session_id, first_user_prompt FROM codex_sessions").fetchall()
    con.close()

    assert len(rows) == 1
    assert rows[0][0] == sid
    assert rows[0][1] == "real first user prompt"


def test_codex_sync_skips_agents_md_when_no_history(tmp_path, monkeypatch):
    """When history.jsonl is missing, the fallback parser skips AGENTS.md
    auto-injected user messages and finds the next real one."""
    root = tmp_path / "personal_db"
    _init_and_install(root)

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    sid = "019dcb33-aaaa-bbbb-cccc-ddddeeee0002"

    session_meta_payload = json.dumps({"id": sid, "timestamp": "2026-04-26T11:00:00Z"})
    # First response_item: synthetic AGENTS.md injection (should be skipped)
    agents_payload = json.dumps(
        {
            "type": "message",
            "role": "user",
            "content": "[{'type': 'input_text', 'text': '# AGENTS.md instructions for /home/user'}]",
        }
    )
    # Second response_item: real user prompt
    real_payload = json.dumps(
        {
            "type": "message",
            "role": "user",
            "content": "[{'type': 'input_text', 'text': 'Fix the bug in main.py'}]",
        }
    )
    assistant_payload = json.dumps(
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Sure, let me fix that."}],
        }
    )

    rollout = sessions_dir / "rollout-test2.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "timestamp": "2026-04-26T11:00:00Z",
                "type": "session_meta",
                "payload": session_meta_payload,
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-04-26T11:00:01Z",
                "type": "response_item",
                "payload": agents_payload,
            }
        )
        + "\n"
        + json.dumps(
            {"timestamp": "2026-04-26T11:00:02Z", "type": "response_item", "payload": real_payload}
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-04-26T11:00:03Z",
                "type": "response_item",
                "payload": assistant_payload,
            }
        )
        + "\n"
    )

    # No history.jsonl — point to nonexistent file
    monkeypatch.setenv("CODEX_SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("CODEX_HISTORY_FILE", str(tmp_path / "no_history.jsonl"))

    cfg = Config(root=root)
    sync_one(cfg, "codex_conversations")

    con = sqlite3.connect(root / "db.sqlite")
    rows = con.execute(
        "SELECT session_id, first_user_prompt, user_msg_count, assistant_msg_count, event_count "
        "FROM codex_sessions"
    ).fetchall()
    con.close()

    assert len(rows) == 1
    session_id, first_user_prompt, user_msg_count, assistant_msg_count, event_count = rows[0]
    assert session_id == sid
    # AGENTS.md injection skipped; real prompt is found
    assert first_user_prompt == "Fix the bug in main.py"
    # Synthetic message doesn't count toward user_msg_count
    assert user_msg_count == 1
    assert assistant_msg_count == 1
    assert event_count == 2
