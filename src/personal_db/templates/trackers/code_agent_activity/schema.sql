CREATE TABLE IF NOT EXISTS code_agent_events (
  agent       TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  timestamp   TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  cwd         TEXT,
  git_branch  TEXT,
  source_file TEXT,
  raw         TEXT,
  -- 1 if Claude Code was running over SSH at hook time; 0 otherwise. Codex
  -- has no direct signal so always 0; engagement viz applies a heuristic.
  is_remote   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (agent, session_id, timestamp, event_type)
);

CREATE INDEX IF NOT EXISTS idx_code_agent_events_ts
  ON code_agent_events(timestamp);

CREATE TABLE IF NOT EXISTS code_agent_intervals (
  agent            TEXT NOT NULL,
  session_id       TEXT NOT NULL,
  start_ts         TEXT NOT NULL,
  end_ts           TEXT NOT NULL,
  state            TEXT NOT NULL,
  duration_seconds REAL NOT NULL,
  cwd              TEXT,
  git_branch       TEXT,
  -- Carried from any event in the session (uniform per session for Claude).
  is_remote        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (agent, session_id, start_ts)
);

CREATE INDEX IF NOT EXISTS idx_code_agent_intervals_state_ts
  ON code_agent_intervals(state, start_ts);
