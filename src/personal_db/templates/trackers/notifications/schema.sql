CREATE TABLE IF NOT EXISTS notifications_events (
  source_record_id TEXT PRIMARY KEY,
  delivered_at     TEXT NOT NULL,
  bundle_id        TEXT,
  app_name         TEXT,
  title            TEXT,
  subtitle         TEXT,
  body             TEXT,
  title_hash       TEXT,
  subtitle_hash    TEXT,
  body_hash        TEXT,
  content_hash     TEXT,
  thread_id        TEXT,
  category_id      TEXT,
  request_id       TEXT,
  source           TEXT NOT NULL DEFAULT 'usernoted',
  imported_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_delivered_at
  ON notifications_events(delivered_at);
CREATE INDEX IF NOT EXISTS idx_notifications_bundle
  ON notifications_events(bundle_id);
CREATE INDEX IF NOT EXISTS idx_notifications_app
  ON notifications_events(app_name);

CREATE TABLE IF NOT EXISTS notification_impacts (
  notification_id    TEXT PRIMARY KEY REFERENCES notifications_events(source_record_id) ON DELETE CASCADE,
  delivered_at       TEXT NOT NULL,
  bundle_id          TEXT,
  app_name           TEXT,
  impact             TEXT NOT NULL,
  confidence         REAL NOT NULL,
  evidence           TEXT,
  batch_count        INTEGER NOT NULL DEFAULT 1,
  prior_event_id     TEXT,
  prior_at           TEXT,
  prior_app_name     TEXT,
  prior_bundle_id    TEXT,
  next_event_id      TEXT,
  next_at            TEXT,
  next_app_name      TEXT,
  next_bundle_id     TEXT,
  acted_at           TEXT,
  seconds_to_action  INTEGER,
  returned_at        TEXT,
  away_seconds       INTEGER,
  computed_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_impacts_delivered_at
  ON notification_impacts(delivered_at);
CREATE INDEX IF NOT EXISTS idx_notification_impacts_impact
  ON notification_impacts(impact);
CREATE INDEX IF NOT EXISTS idx_notification_impacts_app
  ON notification_impacts(app_name);
