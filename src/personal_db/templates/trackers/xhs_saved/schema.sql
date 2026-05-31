CREATE TABLE IF NOT EXISTS xhs_saved_collections (
  collected_at       TEXT PRIMARY KEY,
  source_url         TEXT,
  source_title       TEXT,
  clicked_saved_tab  INTEGER NOT NULL DEFAULT 0,
  note_count         INTEGER NOT NULL DEFAULT 0,
  raw_json           TEXT
);
CREATE INDEX IF NOT EXISTS idx_xhs_saved_collections_collected_at
  ON xhs_saved_collections(collected_at);

CREATE TABLE IF NOT EXISTS xhs_saved_posts (
  note_id             TEXT PRIMARY KEY,
  source_url          TEXT,
  first_seen_url      TEXT,
  xsec_token          TEXT,
  xsec_source         TEXT,
  title               TEXT,
  description         TEXT,
  author_user_id      TEXT,
  author_nickname     TEXT,
  note_type           TEXT,
  posted_at           TEXT,
  thumbnail_url       TEXT,
  image_urls_json     TEXT,
  video_urls_json     TEXT,
  saved_first_seen_at TEXT NOT NULL,
  saved_last_seen_at  TEXT NOT NULL,
  latest_seen_rank    INTEGER,
  last_fetched_at     TEXT,
  fetch_status        TEXT,
  fetch_error         TEXT,
  raw_json            TEXT
);
CREATE INDEX IF NOT EXISTS idx_xhs_saved_posts_saved_first_seen
  ON xhs_saved_posts(saved_first_seen_at);
CREATE INDEX IF NOT EXISTS idx_xhs_saved_posts_posted_at
  ON xhs_saved_posts(posted_at);

CREATE TABLE IF NOT EXISTS xhs_saved_post_snapshots (
  note_id         TEXT NOT NULL,
  snapshot_at     TEXT NOT NULL,
  liked_count     INTEGER,
  collected_count INTEGER,
  comment_count   INTEGER,
  share_count     INTEGER,
  raw_json        TEXT,
  PRIMARY KEY (note_id, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_xhs_saved_post_snapshots_snapshot_at
  ON xhs_saved_post_snapshots(snapshot_at);
