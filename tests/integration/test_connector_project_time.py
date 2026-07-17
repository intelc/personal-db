"""Tests for the derived project_time tracker."""

import json
import sqlite3
from datetime import date

from personal_db.core.config import Config
from personal_db.core.installer import install_template
from personal_db.core.sync import backfill_one


def test_project_time_uses_code_agent_activity_sessions(tmp_path):
    root = tmp_path / "personal_db"
    for subdir in ("trackers", "entities", "notes", "state", "state/oauth"):
        (root / subdir).mkdir(parents=True, exist_ok=True)
    cfg = Config(root=root)
    install_template(cfg, "project_time")

    projects_yaml = cfg.trackers_dir / "project_time" / "projects.yaml"
    projects_yaml.write_text(
        """
cap_session_hours: 4.0
projects:
  - name: personal_db
    repos: []
    cwds: [/repo/personal_db]
    bundle_ids: []
""".lstrip()
    )

    con = sqlite3.connect(cfg.db_path)
    con.executescript(
        """
        CREATE TABLE code_agent_sessions (
          agent TEXT NOT NULL,
          session_id TEXT NOT NULL,
          cwd TEXT,
          started_at TEXT NOT NULL,
          last_msg_at TEXT NOT NULL,
          message_count INTEGER NOT NULL,
          user_msg_count INTEGER NOT NULL,
          assistant_msg_count INTEGER NOT NULL,
          first_user_prompt TEXT,
          source_file TEXT,
          PRIMARY KEY (agent, session_id)
        );
        """
    )
    con.executemany(
        """
        INSERT INTO code_agent_sessions
          (agent, session_id, cwd, started_at, last_msg_at, message_count,
           user_msg_count, assistant_msg_count, first_user_prompt, source_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "claude_code",
                "c1",
                "/repo/personal_db",
                "2026-04-26T12:00:00+00:00",
                "2026-04-26T13:30:00+00:00",
                2,
                1,
                1,
                "fix tracker",
                None,
            ),
            (
                "codex_cli",
                "x1",
                "/repo/personal_db/subdir",
                "2026-04-26T14:00:00+00:00",
                "2026-04-26T15:00:00+00:00",
                2,
                1,
                1,
                "add tests",
                None,
            ),
        ],
    )
    con.commit()
    con.close()

    backfill_one(cfg, "project_time", "2026-04-26", "2026-04-26")

    con = sqlite3.connect(cfg.db_path)
    row = con.execute(
        """
        SELECT hours, breakdown_json
        FROM project_time
        WHERE date = ? AND project = ?
        """,
        (date(2026, 4, 26).isoformat(), "personal_db"),
    ).fetchone()

    assert row is not None
    assert row[0] == 2.5
    assert json.loads(row[1]) == {"claude": 1.5, "codex": 1.0, "app": 0.0}
