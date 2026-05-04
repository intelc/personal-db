CREATE TABLE IF NOT EXISTS granola_documents (
  id               TEXT PRIMARY KEY,
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  duration_seconds INTEGER,
  title            TEXT,
  overview         TEXT,
  content          TEXT,
  transcript       TEXT,
  participants     TEXT,
  created_at       TEXT,
  updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_granola_documents_started_at
  ON granola_documents(started_at);
CREATE INDEX IF NOT EXISTS idx_granola_documents_updated_at
  ON granola_documents(updated_at);
