CREATE TABLE IF NOT EXISTS claude_sessions (
  session_id          TEXT PRIMARY KEY,
  project_slug        TEXT NOT NULL,
  started_at          TEXT NOT NULL,
  last_msg_at         TEXT NOT NULL,
  message_count       INTEGER NOT NULL,
  user_msg_count      INTEGER NOT NULL,
  assistant_msg_count INTEGER NOT NULL,
  first_user_prompt   TEXT
);
CREATE INDEX IF NOT EXISTS idx_claude_sessions_started ON claude_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_claude_sessions_project ON claude_sessions(project_slug);
