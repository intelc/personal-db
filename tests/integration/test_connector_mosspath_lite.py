"""Tests for the mosspath_lite connector."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from personal_db.config import Config
from personal_db.sync import sync_one
from personal_db.ui.viz import discover


def _build_fake_mosspath_db(path: Path) -> None:
    base = int(datetime.now(UTC).timestamp())
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE action_events (
          id TEXT PRIMARY KEY,
          timestamp REAL NOT NULL,
          action_type TEXT NOT NULL,
          app_name TEXT,
          bundle_id TEXT,
          window_title TEXT,
          browser_title TEXT,
          browser_url TEXT,
          browser_domain TEXT,
          focused_role TEXT,
          focused_title TEXT,
          focused_value_preview TEXT,
          clipboard_type TEXT,
          clipboard_preview TEXT,
          key_count INTEGER,
          mouse_count INTEGER,
          scroll_count INTEGER,
          screenshot_path TEXT,
          context_key TEXT,
          note TEXT
        );
        CREATE TABLE session_digests (
          session_id TEXT PRIMARY KEY,
          started_at REAL NOT NULL,
          ended_at REAL NOT NULL,
          payload_json TEXT NOT NULL,
          confidence REAL,
          generated_at REAL
        );
        CREATE TABLE work_episodes (
          id TEXT PRIMARY KEY,
          started_at REAL NOT NULL,
          ended_at REAL NOT NULL,
          source_session_ids TEXT NOT NULL,
          boundary_score_ids TEXT NOT NULL,
          title TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          confidence REAL,
          status TEXT,
          generated_at REAL
        );
        CREATE TABLE routine_answers (
          id TEXT PRIMARY KEY,
          question_id TEXT NOT NULL,
          trigger_mode TEXT NOT NULL,
          started_at REAL NOT NULL,
          ended_at REAL NOT NULL,
          question_title TEXT,
          payload_json TEXT NOT NULL,
          confidence REAL,
          generated_at REAL
        );
        """
    )
    con.execute(
        """
        INSERT INTO action_events VALUES (
          'event-1', ?, 'submitted_text', 'Xcode', 'com.apple.dt.Xcode',
          'TimelineStore.swift', NULL, NULL, NULL, 'AXTextArea', 'Editor',
          'try store.upsertRoutineAnswer(answer)', NULL, NULL, 42, 2, 0,
          'thumbnails/2026-02-02/event-1.jpg', 'ctx-1', 'unit test'
        )
        """,
        (base,),
    )
    con.execute(
        "INSERT INTO session_digests VALUES (?, ?, ?, ?, ?, ?)",
        (
            "session-1",
            base,
            base + 1800,
            json.dumps(
                {
                    "title": "Implementing routine answers",
                    "what": "Worked on Mosspath Lite routine answer storage.",
                    "possibleIntent": "Finish the daily summary bridge",
                    "actions": ["Edited TimelineStore.swift"],
                    "entities": ["Mosspath Lite"],
                    "artifacts": ["TimelineStore.swift"],
                    "apps": ["Xcode"],
                    "domains": [],
                    "evidenceSummary": "Xcode edits and tests.",
                }
            ),
            0.82,
            base + 1900,
        ),
    )
    con.execute(
        "INSERT INTO work_episodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "episode-1",
            base,
            base + 1800,
            "session-1",
            "",
            "Routine answer tracker work",
            json.dumps(
                {
                    "what": "Built a durable activity summary path.",
                    "why": "Expose Mosspath Lite to personal_db",
                    "how": ["Added schema", "Added importer"],
                    "outcome": "Tracker rows are queryable.",
                    "sourceSessionIDs": ["session-1"],
                    "boundaryScoreIDs": [],
                }
            ),
            0.9,
            "draft",
            base + 1900,
        ),
    )
    con.execute(
        "INSERT INTO routine_answers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "routine-1",
            "work_accomplished",
            "daily",
            base,
            base + 1800,
            "What did I work on and accomplish today?",
            json.dumps(
                {
                    "answerMarkdown": "Worked on Mosspath Lite and personal_db integration.",
                    "evidenceIDs": ["session-1", "episode-1"],
                }
            ),
            0.87,
            base + 1900,
        ),
    )
    con.commit()
    con.close()


def test_mosspath_lite_imports_activity_tables(tmp_path, monkeypatch):
    source = tmp_path / "mosspath-lite.sqlite"
    _build_fake_mosspath_db(source)
    monkeypatch.setenv("MOSSPATH_LITE_DB", str(source))

    root = tmp_path / "personal_db"
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
            "mosspath_lite",
        ],
        check=True,
        capture_output=True,
    )

    cfg = Config(root=root)
    sync_one(cfg, "mosspath_lite")
    sync_one(cfg, "mosspath_lite")

    con = sqlite3.connect(cfg.db_path)
    con.row_factory = sqlite3.Row
    assert con.execute("SELECT COUNT(*) FROM mosspath_lite_events").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM mosspath_lite_session_digests").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM mosspath_lite_work_episodes").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM mosspath_lite_routine_answers").fetchone()[0] == 1

    event = con.execute("SELECT * FROM mosspath_lite_events").fetchone()
    assert event["timestamp"]
    assert event["action_type"] == "submitted_text"
    assert event["app_name"] == "Xcode"

    digest = con.execute("SELECT * FROM mosspath_lite_session_digests").fetchone()
    assert digest["title"] == "Implementing routine answers"
    assert json.loads(digest["artifacts_json"]) == ["TimelineStore.swift"]

    answer = con.execute("SELECT * FROM mosspath_lite_routine_answers").fetchone()
    assert answer["question_id"] == "work_accomplished"
    assert "personal_db integration" in answer["answer_markdown"]
    assert json.loads(answer["evidence_ids_json"]) == ["session-1", "episode-1"]

    registry = discover(cfg)
    assert "mosspath_lite:00_today_story" in registry
    assert "mosspath_lite:01_today_apps_domains" in registry
    assert "mosspath_lite:02_activity_heatmap_7d" in registry

    story_html = registry["mosspath_lite:00_today_story"].render(cfg)
    assert "What did I work on and accomplish today?" in story_html
    assert "Routine answer tracker work" in story_html

    apps_html = registry["mosspath_lite:01_today_apps_domains"].render(cfg)
    assert "Xcode" in apps_html

    heatmap_html = registry["mosspath_lite:02_activity_heatmap_7d"].render(cfg)
    assert "heatmap" in heatmap_html
