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
