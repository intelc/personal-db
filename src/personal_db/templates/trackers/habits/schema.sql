CREATE TABLE IF NOT EXISTS habits (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  name  TEXT NOT NULL,
  value TEXT,
  ts    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_habits_name_ts ON habits(name, ts);
