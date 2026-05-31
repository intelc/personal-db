CREATE TABLE IF NOT EXISTS xhs_notes (
  note_id         TEXT PRIMARY KEY,
  xhs_user_id     TEXT,
  author_nickname TEXT,
  note_type       TEXT,
  title           TEXT,
  description     TEXT,
  permalink       TEXT,
  thumbnail_url   TEXT,
  posted_at       TEXT NOT NULL,
  visibility_label TEXT,
  is_archived     INTEGER NOT NULL DEFAULT 0,
  creator_last_seen_at TEXT,
  first_seen_at   TEXT NOT NULL,
  last_fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xhs_notes_posted_at ON xhs_notes(posted_at);

CREATE TABLE IF NOT EXISTS xhs_note_snapshots (
  note_id         TEXT NOT NULL,
  snapshot_at     TEXT NOT NULL,
  view_count      INTEGER,
  liked_count     INTEGER,
  collected_count INTEGER,
  comment_count   INTEGER,
  share_count     INTEGER,
  raw_json        TEXT,
  PRIMARY KEY (note_id, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_xhs_note_snapshots_snapshot_at
  ON xhs_note_snapshots(snapshot_at);

CREATE TABLE IF NOT EXISTS xhs_account_snapshots (
  profile_url             TEXT NOT NULL,
  snapshot_at             TEXT NOT NULL,
  nickname                TEXT,
  following_count         INTEGER,
  followers_count         INTEGER,
  liked_collected_count   INTEGER,
  visible_note_count      INTEGER,
  raw_json                TEXT,
  PRIMARY KEY (profile_url, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_xhs_account_snapshots_snapshot_at
  ON xhs_account_snapshots(snapshot_at);
