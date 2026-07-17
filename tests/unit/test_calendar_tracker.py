from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, init_db
from personal_db.core.tracker import Tracker

ROOT = Path(__file__).resolve().parents[2]
CALENDAR_DIR = ROOT / "src" / "personal_db" / "templates" / "trackers" / "calendar"


def _cocoa(dt: datetime) -> float:
    epoch = datetime(2001, 1, 1, tzinfo=UTC)
    return (dt - epoch).total_seconds()


def _make_calendar_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE ZCALENDAR(
          Z_PK INTEGER PRIMARY KEY,
          ZTITLE TEXT
        );
        CREATE TABLE ZCALENDARITEM(
          Z_PK INTEGER PRIMARY KEY,
          ZUUID TEXT,
          ZCALENDAR INTEGER,
          ZTITLE TEXT,
          ZLOCATION TEXT,
          ZNOTES TEXT,
          ZSTARTDATE REAL,
          ZENDDATE REAL,
          ZALLDAY INTEGER,
          ZTIMEZONE TEXT
        );
        """
    )
    con.execute("INSERT INTO ZCALENDAR(Z_PK, ZTITLE) VALUES (1, 'Work')")
    con.execute(
        """
        INSERT INTO ZCALENDARITEM(
          Z_PK, ZUUID, ZCALENDAR, ZTITLE, ZLOCATION, ZNOTES,
          ZSTARTDATE, ZENDDATE, ZALLDAY, ZTIMEZONE
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            11,
            "event-uuid",
            1,
            "Deep work",
            "Desk",
            "private notes",
            _cocoa(datetime(2026, 5, 30, 16, 0, tzinfo=UTC)),
            _cocoa(datetime(2026, 5, 30, 17, 0, tzinfo=UTC)),
            0,
            "UTC",
        ),
    )
    con.commit()
    con.close()


def _tracker(tmp_root: Path) -> Tracker:
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (CALENDAR_DIR / "schema.sql").read_text())
    return Tracker("calendar", cfg, manifest=None)


def test_sync_reads_coredata_calendar_and_hashes_notes(tmp_root, tmp_path, monkeypatch):
    from personal_db.templates.trackers.calendar import ingest

    source = tmp_path / "Calendar.sqlitedb"
    _make_calendar_db(source)
    monkeypatch.setenv("PERSONAL_DB_CALENDAR_DB", str(source))

    t = _tracker(tmp_root)
    ingest.sync(t)

    con = sqlite3.connect(t.cfg.db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM calendar_events").fetchone()
    assert row["title"] == "Deep work"
    assert row["calendar_title"] == "Work"
    assert row["location"] == "Desk"
    assert row["notes_hash"]
    assert row["start_at"] == "2026-05-30T16:00:00+00:00"
    assert row["end_at"] == "2026-05-30T17:00:00+00:00"
    assert row["all_day"] == 0
    con.close()


def test_sync_materializes_reality_blocks_from_activity(tmp_root, tmp_path, monkeypatch):
    from personal_db.templates.trackers.calendar import ingest

    source = tmp_path / "Calendar.sqlitedb"
    _make_calendar_db(source)
    monkeypatch.setenv("PERSONAL_DB_CALENDAR_DB", str(source))

    t = _tracker(tmp_root)
    con = sqlite3.connect(t.cfg.db_path)
    con.executescript(
        """
        CREATE TABLE screen_time_app_usage(
          id INTEGER PRIMARY KEY,
          bundle_id TEXT NOT NULL,
          start_at TEXT NOT NULL,
          end_at TEXT NOT NULL,
          seconds INTEGER NOT NULL,
          UNIQUE(bundle_id, start_at)
        );
        CREATE TABLE screen_time_app_names(
          bundle_id TEXT PRIMARY KEY,
          app_name TEXT NOT NULL,
          resolved_at TEXT NOT NULL
        );
        CREATE TABLE mosspath_lite_events(
          id TEXT PRIMARY KEY,
          timestamp TEXT NOT NULL,
          action_type TEXT NOT NULL,
          app_name TEXT,
          bundle_id TEXT,
          browser_domain TEXT
        );
        CREATE TABLE chrome_visits(
          visit_id INTEGER NOT NULL,
          profile TEXT NOT NULL,
          url TEXT,
          title TEXT,
          domain TEXT,
          visited_at TEXT,
          duration_seconds REAL,
          transition INTEGER,
          PRIMARY KEY (visit_id, profile)
        );
        INSERT INTO screen_time_app_names(bundle_id, app_name, resolved_at)
        VALUES ('com.cursor.Cursor', 'Cursor', '2026-05-30T16:00:00+00:00');
        INSERT INTO screen_time_app_usage(bundle_id, start_at, end_at, seconds)
        VALUES ('com.cursor.Cursor', '2026-05-30T16:05:00+00:00', '2026-05-30T16:45:00+00:00', 2400);
        INSERT INTO mosspath_lite_events(id, timestamp, action_type, app_name, bundle_id, browser_domain)
        VALUES
          ('e1', '2026-05-30T16:10:00+00:00', 'app_visit', 'Cursor', 'com.cursor.Cursor', NULL),
          ('e2', '2026-05-30T16:20:00+00:00', 'app_visit', 'Chrome', 'com.google.Chrome', 'github.com'),
          ('e3', '2026-05-30T16:25:00+00:00', 'app_visit', 'Slack', 'com.tinyspeck.slackmacgap', NULL);
        INSERT INTO chrome_visits(visit_id, profile, domain, visited_at)
        VALUES (1, 'Default', 'github.com', '2026-05-30T16:22:00+00:00');
        """
    )
    con.commit()
    con.close()

    ingest.sync(t)

    con = sqlite3.connect(t.cfg.db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM calendar_reality_blocks").fetchone()
    assert row["planned_minutes"] == 60
    assert row["screen_time_minutes"] == 40
    assert row["actual_minutes"] == 40
    assert row["mosspath_events"] == 3
    assert row["chrome_visits"] == 1
    assert row["reality_label"] == "fragmented"
    assert "Cursor" in row["top_apps_json"]
    assert "github.com" in row["top_domains_json"]
    con.close()


def test_all_day_events_do_not_absorb_day_activity(tmp_root, tmp_path, monkeypatch):
    from personal_db.templates.trackers.calendar import ingest

    source = tmp_path / "Calendar.sqlitedb"
    _make_calendar_db(source)
    con = sqlite3.connect(source)
    con.execute(
        """
        INSERT INTO ZCALENDARITEM(
          Z_PK, ZUUID, ZCALENDAR, ZTITLE, ZLOCATION, ZNOTES,
          ZSTARTDATE, ZENDDATE, ZALLDAY, ZTIMEZONE
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            12,
            "all-day-uuid",
            1,
            "Company holiday",
            "",
            "",
            _cocoa(datetime(2026, 5, 30, 0, 0, tzinfo=UTC)),
            _cocoa(datetime(2026, 5, 31, 0, 0, tzinfo=UTC)),
            1,
            "UTC",
        ),
    )
    con.commit()
    con.close()
    monkeypatch.setenv("PERSONAL_DB_CALENDAR_DB", str(source))

    t = _tracker(tmp_root)
    con = sqlite3.connect(t.cfg.db_path)
    con.executescript(
        """
        CREATE TABLE screen_time_app_usage(
          id INTEGER PRIMARY KEY,
          bundle_id TEXT NOT NULL,
          start_at TEXT NOT NULL,
          end_at TEXT NOT NULL,
          seconds INTEGER NOT NULL,
          UNIQUE(bundle_id, start_at)
        );
        INSERT INTO screen_time_app_usage(bundle_id, start_at, end_at, seconds)
        VALUES ('com.apple.Terminal', '2026-05-30T09:00:00+00:00', '2026-05-30T17:00:00+00:00', 28800);
        """
    )
    con.commit()
    con.close()

    ingest.sync(t)

    con = sqlite3.connect(t.cfg.db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM calendar_reality_blocks WHERE title = 'Company holiday'"
    ).fetchone()
    assert row["reality_label"] == "calendar_only"
    assert row["actual_minutes"] == 0
    assert row["screen_time_minutes"] == 0
    assert row["app_count"] == 0
    assert row["top_apps_json"] == "[]"
    con.close()


def test_calendar_reality_app_views_render_with_rows(tmp_root, tmp_path, monkeypatch):
    from personal_db.core.apps import AppContext, discover_apps
    from personal_db.templates.apps.calendar_reality import views
    from personal_db.templates.trackers.calendar import ingest

    source = tmp_path / "Calendar.sqlitedb"
    _make_calendar_db(source)
    monkeypatch.setenv("PERSONAL_DB_CALENDAR_DB", str(source))

    t = _tracker(tmp_root)
    ingest.sync(t)
    app = discover_apps(t.cfg)["calendar_reality"]
    ctx = AppContext(cfg=t.cfg, app_dir=app.root, manifest=app.manifest)
    assert "Blocks" in views.render_overview(ctx)
    assert "Deep work" in views.render_blocks(ctx)
