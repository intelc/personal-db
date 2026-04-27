CREATE TABLE IF NOT EXISTS daily_time_accounting (
  date     TEXT NOT NULL,    -- YYYY-MM-DD local date
  category TEXT NOT NULL,    -- "sleep", "workout", category from yaml, or "_unaccounted"
  hours    REAL,
  PRIMARY KEY (date, category)
);
CREATE INDEX IF NOT EXISTS idx_daily_time_date ON daily_time_accounting(date);
