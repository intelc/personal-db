CREATE TABLE IF NOT EXISTS life_context (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  date      TEXT NOT NULL,
  state     TEXT,
  note      TEXT,
  logged_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_life_context_date  ON life_context(date);
CREATE INDEX IF NOT EXISTS idx_life_context_state ON life_context(state);
