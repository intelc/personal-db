CREATE TABLE IF NOT EXISTS github_commits (
  sha          TEXT PRIMARY KEY,
  repo         TEXT,
  committed_at TEXT NOT NULL,
  message      TEXT,
  additions    INTEGER,
  deletions    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_github_commits_committed_at
  ON github_commits(committed_at);
