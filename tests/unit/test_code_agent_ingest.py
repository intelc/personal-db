from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from personal_db.config import Config
from personal_db.tracker import Tracker
from personal_db.installer import install_template


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    root = tmp_path / "personal_db"
    for sub in ("trackers", "entities", "notes", "state", "state/oauth"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    cfg = Config(root=root)
    install_template(cfg, "code_agent_activity")
    # Apply schema.sql
    schema_sql = (cfg.trackers_dir / "code_agent_activity" / "schema.sql").read_text()
    con = sqlite3.connect(cfg.db_path)
    con.executescript(schema_sql)
    con.commit()
    con.close()
    return cfg


def _hooks_log(cfg: Config) -> Path:
    return cfg.state_dir / "code_agent_hooks.jsonl"


def test_sync_ingests_claude_hooks(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(_hooks_log(cfg)))
    monkeypatch.setenv("CODEX_HOME", str(cfg.root / "fake_codex"))
    log = _hooks_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "\n".join(
            [
                json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00", "cwd": "/p"}),
                json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "received_at": "2026-05-09T10:00:05.000+00:00"}),
                json.dumps({"hook_event_name": "Stop", "session_id": "s1", "received_at": "2026-05-09T10:00:30.000+00:00"}),
                json.dumps({"hook_event_name": "SessionEnd", "session_id": "s1", "received_at": "2026-05-09T10:01:00.000+00:00"}),
                "",
            ]
        )
    )

    from personal_db.templates.trackers.code_agent_activity import ingest
    from personal_db.manifest import load_manifest

    manifest = load_manifest(cfg.trackers_dir / "code_agent_activity" / "manifest.yaml")
    t = Tracker(name="code_agent_activity", cfg=cfg, manifest=manifest)
    ingest.sync(t)

    con = sqlite3.connect(cfg.db_path)
    events = con.execute("SELECT event_type FROM code_agent_events ORDER BY timestamp").fetchall()
    assert [r[0] for r in events] == ["session_start", "prompt_submitted", "awaiting_user", "session_ended"]

    intervals = con.execute(
        "SELECT state, start_ts, end_ts FROM code_agent_intervals ORDER BY start_ts"
    ).fetchall()
    assert [r[0] for r in intervals] == ["awaiting_user", "agent_running", "awaiting_user"]
    con.close()


def test_sync_is_idempotent(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(_hooks_log(cfg)))
    monkeypatch.setenv("CODEX_HOME", str(cfg.root / "fake_codex"))
    log = _hooks_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00"}) + "\n"
    )

    from personal_db.templates.trackers.code_agent_activity import ingest
    from personal_db.manifest import load_manifest

    manifest = load_manifest(cfg.trackers_dir / "code_agent_activity" / "manifest.yaml")
    t = Tracker(name="code_agent_activity", cfg=cfg, manifest=manifest)

    ingest.sync(t)
    ingest.sync(t)  # second run should be a no-op

    con = sqlite3.connect(cfg.db_path)
    n = con.execute("SELECT COUNT(*) FROM code_agent_events").fetchone()[0]
    assert n == 1


def test_sync_handles_malformed_line(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(_hooks_log(cfg)))
    monkeypatch.setenv("CODEX_HOME", str(cfg.root / "fake_codex"))
    log = _hooks_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "this is not json\n"
        + json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00"})
        + "\n"
    )

    from personal_db.templates.trackers.code_agent_activity import ingest
    from personal_db.manifest import load_manifest

    manifest = load_manifest(cfg.trackers_dir / "code_agent_activity" / "manifest.yaml")
    t = Tracker(name="code_agent_activity", cfg=cfg, manifest=manifest)
    ingest.sync(t)  # must not raise

    con = sqlite3.connect(cfg.db_path)
    n = con.execute("SELECT COUNT(*) FROM code_agent_events").fetchone()[0]
    assert n == 1


def test_sync_resumes_from_cursor(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(_hooks_log(cfg)))
    monkeypatch.setenv("CODEX_HOME", str(cfg.root / "fake_codex"))
    log = _hooks_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00"}) + "\n"
    )

    from personal_db.templates.trackers.code_agent_activity import ingest
    from personal_db.manifest import load_manifest

    manifest = load_manifest(cfg.trackers_dir / "code_agent_activity" / "manifest.yaml")
    t = Tracker(name="code_agent_activity", cfg=cfg, manifest=manifest)
    ingest.sync(t)

    # Append a new line; sync should pick up only the new one.
    with log.open("a") as fh:
        fh.write(
            json.dumps(
                {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "received_at": "2026-05-09T10:00:05.000+00:00"}
            )
            + "\n"
        )
    ingest.sync(t)

    con = sqlite3.connect(cfg.db_path)
    n = con.execute("SELECT COUNT(*) FROM code_agent_events").fetchone()[0]
    assert n == 2


def test_sync_dedupes_codex_multi_session_meta(cfg: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Real Codex rollout files emit multiple session_meta rows per session
    (resume points). Materialized intervals must not see spurious mid-session
    `awaiting_user` transitions caused by the duplicate session_starts."""
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(_hooks_log(cfg)))
    codex_root = tmp_path / "codex_home" / "sessions" / "2026" / "05" / "09"
    codex_root.mkdir(parents=True)
    (codex_root / "rollout-test.jsonl").write_text(
        "\n".join([
            json.dumps({"timestamp": "2026-05-09T10:00:00.000Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/p"}}),
            json.dumps({"timestamp": "2026-05-09T10:00:05.000Z", "type": "event_msg", "payload": {"type": "user_message"}}),
            # Second session_meta arrives mid-session (the bug we're fixing)
            json.dumps({"timestamp": "2026-05-09T10:00:10.000Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/p"}}),
            json.dumps({"timestamp": "2026-05-09T10:00:30.000Z", "type": "event_msg", "payload": {"type": "task_complete"}}),
        ]) + "\n"
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex_home"))

    from personal_db.templates.trackers.code_agent_activity import ingest
    from personal_db.manifest import load_manifest
    manifest = load_manifest(cfg.trackers_dir / "code_agent_activity" / "manifest.yaml")
    t = Tracker(name="code_agent_activity", cfg=cfg, manifest=manifest)
    ingest.sync(t)

    con = sqlite3.connect(cfg.db_path)
    # The events table records both session_meta rows (forensic data).
    starts = con.execute("SELECT COUNT(*) FROM code_agent_events WHERE event_type='session_start' AND session_id='s1'").fetchone()[0]
    assert starts == 2

    # Intervals must reflect the dedupe: agent should be running from
    # 10:00:05 (prompt_submitted) all the way to 10:00:30 (awaiting_user),
    # NOT split at the spurious 10:00:10 session_start.
    intervals = con.execute(
        "SELECT state, start_ts, end_ts, ROUND(duration_seconds) AS dur "
        "FROM code_agent_intervals WHERE session_id='s1' ORDER BY start_ts"
    ).fetchall()
    con.close()

    states = [r[0] for r in intervals]
    durations = [r[3] for r in intervals]
    # Synthetic session_ended adds a trailing 1s awaiting_user. The middle
    # agent_running interval is what matters: with dedup it spans the full
    # turn (T1=10:00:05 → T3=10:00:30 = 25s). Pre-fix it would split at the
    # spurious session_start, giving agent_running of only 5s.
    assert states == ["awaiting_user", "agent_running", "awaiting_user"], (
        f"got {states} / {durations}"
    )
    agent_running_dur = next(r[3] for r in intervals if r[0] == "agent_running")
    assert agent_running_dur == 25, (
        f"agent_running duration = {agent_running_dur} (pre-fix bug would give 5)"
    )
