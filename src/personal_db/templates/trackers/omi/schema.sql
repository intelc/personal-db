CREATE TABLE IF NOT EXISTS omi_conversations (
  id               TEXT PRIMARY KEY,
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  duration_seconds INTEGER,
  title            TEXT,
  overview         TEXT,
  transcript       TEXT,
  action_items     TEXT,
  category         TEXT,
  source           TEXT
);
CREATE INDEX IF NOT EXISTS idx_omi_conversations_started_at
  ON omi_conversations(started_at);
