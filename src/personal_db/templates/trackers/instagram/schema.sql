CREATE TABLE IF NOT EXISTS reels_media (
  media_id           TEXT PRIMARY KEY,
  ig_user_id         TEXT NOT NULL,
  media_product_type TEXT,
  media_type         TEXT,
  permalink          TEXT,
  caption            TEXT,
  thumbnail_url      TEXT,
  timestamp          TEXT NOT NULL,
  fetched_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reels_media_timestamp ON reels_media(timestamp);
CREATE INDEX IF NOT EXISTS idx_reels_media_product_type ON reels_media(media_product_type);

CREATE TABLE IF NOT EXISTS instagram_account_snapshots (
  ig_user_id      TEXT NOT NULL,
  snapshot_at     TEXT NOT NULL,
  username        TEXT,
  name            TEXT,
  account_type    TEXT,
  media_count     INTEGER,
  followers_count INTEGER,
  follows_count   INTEGER,
  raw_json        TEXT,
  PRIMARY KEY (ig_user_id, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_instagram_account_snapshots_snapshot_at
  ON instagram_account_snapshots(snapshot_at);

CREATE TABLE IF NOT EXISTS reels_insights_snapshots (
  media_id                          TEXT NOT NULL,
  snapshot_at                       TEXT NOT NULL,
  views                             INTEGER,
  reach                             INTEGER,
  likes                             INTEGER,
  comments                          INTEGER,
  shares                            INTEGER,
  saved                             INTEGER,
  reposts                           INTEGER,
  total_interactions                INTEGER,
  ig_reels_avg_watch_time_ms        INTEGER,
  ig_reels_video_view_total_time_ms INTEGER,
  reels_skip_rate_pct               REAL,
  crossposted_views                 INTEGER,
  facebook_views                    INTEGER,
  PRIMARY KEY (media_id, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_reels_snapshots_snapshot_at ON reels_insights_snapshots(snapshot_at);
