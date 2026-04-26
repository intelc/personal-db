CREATE TABLE IF NOT EXISTS chrome_visits (
  visit_id         INTEGER NOT NULL,
  profile          TEXT NOT NULL,
  url              TEXT,
  title            TEXT,
  domain           TEXT,
  visited_at       TEXT,
  duration_seconds REAL,
  transition       INTEGER,
  PRIMARY KEY (visit_id, profile)
);
CREATE INDEX IF NOT EXISTS idx_chrome_visits_visited_at ON chrome_visits(visited_at);
CREATE INDEX IF NOT EXISTS idx_chrome_visits_domain     ON chrome_visits(domain);
