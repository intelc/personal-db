CREATE TABLE IF NOT EXISTS calendar_events (
  event_id        TEXT PRIMARY KEY,
  source          TEXT NOT NULL,
  source_db       TEXT,
  source_pk       TEXT,
  calendar_id     TEXT,
  calendar_title  TEXT,
  title           TEXT,
  location        TEXT,
  notes_hash      TEXT,
  start_at        TEXT NOT NULL,
  end_at          TEXT NOT NULL,
  all_day         INTEGER NOT NULL DEFAULT 0,
  timezone        TEXT,
  url             TEXT,
  status          TEXT,
  availability    TEXT,
  imported_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calendar_events_start_at
  ON calendar_events(start_at);
CREATE INDEX IF NOT EXISTS idx_calendar_events_calendar
  ON calendar_events(calendar_title);

CREATE TABLE IF NOT EXISTS calendar_reality_blocks (
  event_id             TEXT PRIMARY KEY REFERENCES calendar_events(event_id) ON DELETE CASCADE,
  date                 TEXT NOT NULL,
  title                TEXT,
  calendar_title       TEXT,
  start_at             TEXT NOT NULL,
  end_at               TEXT NOT NULL,
  planned_minutes      INTEGER NOT NULL,
  actual_minutes       INTEGER NOT NULL DEFAULT 0,
  screen_time_minutes  INTEGER NOT NULL DEFAULT 0,
  mosspath_events      INTEGER NOT NULL DEFAULT 0,
  chrome_visits        INTEGER NOT NULL DEFAULT 0,
  app_count            INTEGER NOT NULL DEFAULT 0,
  domain_count         INTEGER NOT NULL DEFAULT 0,
  top_apps_json        TEXT,
  top_domains_json     TEXT,
  projects_json        TEXT,
  reality_label        TEXT NOT NULL,
  fragmentation_score  REAL NOT NULL DEFAULT 0,
  computed_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calendar_reality_date
  ON calendar_reality_blocks(date);
CREATE INDEX IF NOT EXISTS idx_calendar_reality_label
  ON calendar_reality_blocks(reality_label);
