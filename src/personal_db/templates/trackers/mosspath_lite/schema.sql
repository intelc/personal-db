CREATE TABLE IF NOT EXISTS mosspath_lite_events (
  id                TEXT PRIMARY KEY,
  timestamp         TEXT NOT NULL,
  action_type       TEXT NOT NULL,
  app_name          TEXT,
  bundle_id         TEXT,
  window_title      TEXT,
  browser_title     TEXT,
  browser_url       TEXT,
  browser_domain    TEXT,
  focused_role      TEXT,
  focused_title     TEXT,
  focused_preview   TEXT,
  clipboard_type    TEXT,
  clipboard_preview TEXT,
  key_count         INTEGER,
  mouse_count       INTEGER,
  scroll_count      INTEGER,
  screenshot_path   TEXT,
  context_key       TEXT,
  note              TEXT
);
CREATE INDEX IF NOT EXISTS idx_mosspath_lite_events_timestamp ON mosspath_lite_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_mosspath_lite_events_app ON mosspath_lite_events(app_name);
CREATE INDEX IF NOT EXISTS idx_mosspath_lite_events_domain ON mosspath_lite_events(browser_domain);

CREATE TABLE IF NOT EXISTS mosspath_lite_session_digests (
  session_id       TEXT PRIMARY KEY,
  started_at       TEXT NOT NULL,
  ended_at         TEXT NOT NULL,
  title            TEXT,
  what             TEXT,
  possible_intent  TEXT,
  actions_json     TEXT,
  entities_json    TEXT,
  artifacts_json   TEXT,
  apps_json        TEXT,
  domains_json     TEXT,
  evidence_summary TEXT,
  confidence       REAL,
  generated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_mosspath_lite_sessions_started ON mosspath_lite_session_digests(started_at);

CREATE TABLE IF NOT EXISTS mosspath_lite_work_episodes (
  id                       TEXT PRIMARY KEY,
  started_at               TEXT NOT NULL,
  ended_at                 TEXT NOT NULL,
  title                    TEXT,
  what                     TEXT,
  why                      TEXT,
  how_json                 TEXT,
  outcome                  TEXT,
  source_session_ids_json  TEXT,
  boundary_score_ids_json  TEXT,
  confidence               REAL,
  status                   TEXT,
  generated_at             TEXT
);
CREATE INDEX IF NOT EXISTS idx_mosspath_lite_episodes_started ON mosspath_lite_work_episodes(started_at);

CREATE TABLE IF NOT EXISTS mosspath_lite_routine_answers (
  id                TEXT PRIMARY KEY,
  question_id       TEXT NOT NULL,
  question_title    TEXT,
  trigger_mode      TEXT NOT NULL,
  started_at        TEXT NOT NULL,
  ended_at          TEXT NOT NULL,
  answer_markdown   TEXT,
  evidence_ids_json TEXT,
  confidence        REAL,
  generated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_mosspath_lite_answers_ended ON mosspath_lite_routine_answers(ended_at);
