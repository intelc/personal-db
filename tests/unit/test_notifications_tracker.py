from __future__ import annotations

import plistlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, init_db
from personal_db.core.tracker import Tracker

ROOT = Path(__file__).resolve().parents[2]
NOTIFICATIONS_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "notifications"


def _cocoa(dt: datetime) -> float:
    epoch = datetime(2001, 1, 1, tzinfo=UTC)
    return (dt - epoch).total_seconds()


def _payload(title: str, subtitle: str = "", body: str = "", thread_id: str = "") -> bytes:
    return plistlib.dumps(
        {
            "req": {
                "titl": title,
                "subt": subtitle,
                "body": body,
                "threadIdentifier": thread_id,
                "categoryIdentifier": "message",
            }
        },
        fmt=plistlib.FMT_BINARY,
    )


def _make_usernoted_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE app(app_id INTEGER PRIMARY KEY, identifier TEXT);
        CREATE TABLE record(
          app_id INTEGER,
          data BLOB,
          delivered_date REAL
        );
        """
    )
    con.execute("INSERT INTO app(app_id, identifier) VALUES (1, 'com.tinyspeck.slackmacgap')")
    con.execute("INSERT INTO app(app_id, identifier) VALUES (2, 'com.apple.mail')")
    con.execute(
        "INSERT INTO record(app_id, data, delivered_date) VALUES (?, ?, ?)",
        (
            1,
            _payload("Ada", body="can you look at this?", thread_id="slack-thread"),
            _cocoa(datetime(2026, 5, 30, 10, 1, tzinfo=UTC)),
        ),
    )
    con.execute(
        "INSERT INTO record(app_id, data, delivered_date) VALUES (?, ?, ?)",
        (
            2,
            _payload("Receipt", body="your statement is ready"),
            _cocoa(datetime(2026, 5, 30, 10, 20, tzinfo=UTC)),
        ),
    )
    con.commit()
    con.close()


def _tracker(tmp_root: Path) -> Tracker:
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (NOTIFICATIONS_DIR / "schema.sql").read_text())
    return Tracker("notifications", cfg, manifest=None)


def test_sync_reads_usernoted_with_content_redacted(tmp_root, tmp_path, monkeypatch):
    from personal_db.templates.trackers.notifications import ingest

    source = tmp_path / "usernoted.sqlite"
    _make_usernoted_db(source)
    monkeypatch.setenv("PERSONAL_DB_NOTIFICATIONS_DB", str(source))
    monkeypatch.delenv("PERSONAL_DB_NOTIFICATIONS_STORE_CONTENT", raising=False)
    monkeypatch.setattr(ingest, "resolve_app_name", lambda bundle_id: f"App {bundle_id}")

    t = _tracker(tmp_root)
    ingest.sync(t)

    con = sqlite3.connect(t.cfg.db_path)
    con.row_factory = sqlite3.Row
    rows = list(con.execute("SELECT * FROM notifications_events ORDER BY delivered_at"))
    assert len(rows) == 2
    assert rows[0]["app_name"] == "App com.tinyspeck.slackmacgap"
    assert rows[0]["title"] is None
    assert rows[0]["body"] is None
    assert rows[0]["title_hash"]
    assert rows[0]["body_hash"]
    assert rows[0]["thread_id"] == "slack-thread"
    assert t.cursor.get() == "2026-05-30T10:20:00+00:00"
    con.close()


def test_sync_materializes_impact_from_mosspath_events(tmp_root, tmp_path, monkeypatch):
    from personal_db.templates.trackers.notifications import ingest

    source = tmp_path / "usernoted.sqlite"
    _make_usernoted_db(source)
    monkeypatch.setenv("PERSONAL_DB_NOTIFICATIONS_DB", str(source))
    monkeypatch.setattr(ingest, "resolve_app_name", lambda bundle_id: bundle_id)

    t = _tracker(tmp_root)
    con = sqlite3.connect(t.cfg.db_path)
    con.executescript(
        """
        CREATE TABLE mosspath_lite_events(
          id TEXT PRIMARY KEY,
          timestamp TEXT NOT NULL,
          app_name TEXT,
          bundle_id TEXT
        );
        INSERT INTO mosspath_lite_events(id, timestamp, app_name, bundle_id)
        VALUES
          ('before', '2026-05-30T10:00:00+00:00', 'Cursor', 'com.cursor.Cursor'),
          ('slack',  '2026-05-30T10:01:30+00:00', 'Slack', 'com.tinyspeck.slackmacgap');
        """
    )
    con.commit()
    con.close()

    ingest.sync(t)

    con = sqlite3.connect(t.cfg.db_path)
    impacts = dict(con.execute("SELECT bundle_id, impact FROM notification_impacts").fetchall())
    assert impacts["com.tinyspeck.slackmacgap"] == "derailed"
    assert impacts["com.apple.mail"] == "ignored"
    con.close()


def test_payload_parser_accepts_usernoted_req_blob():
    from personal_db.templates.trackers.notifications.ingest import _parse_payload

    parsed = _parse_payload(_payload("Sender", subtitle="Thread", body="hello"))
    assert parsed["title"] == "Sender"
    assert parsed["subtitle"] == "Thread"
    assert parsed["body"] == "hello"
    assert parsed["category_id"] == "message"
