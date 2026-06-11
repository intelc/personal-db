CREATE TABLE IF NOT EXISTS app_places_settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_places_aliases (
  place_name TEXT PRIMARY KEY,
  alias      TEXT NOT NULL,
  hidden     INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_places_aliases_hidden
  ON app_places_aliases(hidden);

INSERT OR IGNORE INTO app_places_settings(key, value, updated_at)
VALUES
  ('blur_precision_m', '0', datetime('now')),
  ('hide_coordinates', '0', datetime('now')),
  ('default_days', '30', datetime('now'));
