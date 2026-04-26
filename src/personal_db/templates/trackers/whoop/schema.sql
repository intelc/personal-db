CREATE TABLE IF NOT EXISTS whoop_cycles (
  id                 TEXT PRIMARY KEY,
  start              TEXT NOT NULL,
  end                TEXT,
  strain             REAL,
  average_heart_rate INTEGER
);
CREATE INDEX IF NOT EXISTS idx_whoop_cycles_start ON whoop_cycles(start);
