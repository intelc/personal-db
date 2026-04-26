CREATE TABLE IF NOT EXISTS screen_time_app_usage (
  id        INTEGER PRIMARY KEY,
  bundle_id TEXT NOT NULL,
  start_at  TEXT NOT NULL,
  end_at    TEXT NOT NULL,
  seconds   INTEGER NOT NULL,
  UNIQUE(bundle_id, start_at)
);
CREATE INDEX IF NOT EXISTS idx_screen_time_start ON screen_time_app_usage(start_at);
