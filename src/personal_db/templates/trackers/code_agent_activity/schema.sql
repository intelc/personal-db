CREATE TABLE IF NOT EXISTS code_agent_events (
  agent       TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  timestamp   TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  cwd         TEXT,
  git_branch  TEXT,
  source_file TEXT,
  raw         TEXT,
  PRIMARY KEY (agent, session_id, timestamp, event_type)
);

CREATE INDEX IF NOT EXISTS idx_code_agent_events_session
  ON code_agent_events(agent, session_id);
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
  PRIMARY KEY (agent, session_id, start_ts)
);

CREATE INDEX IF NOT EXISTS idx_code_agent_intervals_state_ts
  ON code_agent_intervals(state, start_ts);
