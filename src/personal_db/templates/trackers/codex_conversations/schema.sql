CREATE TABLE IF NOT EXISTS codex_sessions (
  session_id          TEXT PRIMARY KEY,
  cwd                 TEXT,
  started_at          TEXT NOT NULL,
  last_event_at       TEXT NOT NULL,
  event_count         INTEGER NOT NULL,
  user_msg_count      INTEGER NOT NULL,
  assistant_msg_count INTEGER NOT NULL,
  first_user_prompt   TEXT
);
CREATE INDEX IF NOT EXISTS idx_codex_sessions_started ON codex_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_codex_sessions_cwd ON codex_sessions(cwd);
