CREATE TABLE IF NOT EXISTS subscription_match_rules (
  rule_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  subscription_id TEXT,
  merchant_pattern TEXT NOT NULL,
  label          TEXT,
  domain_pattern TEXT,
  app_pattern    TEXT,
  bundle_id      TEXT,
  enabled        INTEGER NOT NULL DEFAULT 1,
  source         TEXT NOT NULL DEFAULT 'system',
  updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_subscription_match_rules_subscription
  ON subscription_match_rules(subscription_id);
CREATE INDEX IF NOT EXISTS idx_subscription_match_rules_enabled
  ON subscription_match_rules(enabled);

CREATE TABLE IF NOT EXISTS subscription_entities (
  subscription_id    TEXT PRIMARY KEY,
  label              TEXT NOT NULL,
  merchant_key       TEXT NOT NULL,
  series_key         TEXT,
  typical_amount     REAL,
  amount_min         REAL,
  amount_max         REAL,
  expected_day       INTEGER,
  first_charge_date  TEXT,
  last_charge_date   TEXT,
  charge_count       INTEGER NOT NULL DEFAULT 0,
  avg_amount         REAL,
  monthly_avg_amount REAL,
  latest_amount      REAL,
  cadence            TEXT,
  next_expected_date TEXT,
  status             TEXT NOT NULL DEFAULT 'active',
  confidence         REAL NOT NULL DEFAULT 0,
  source_flags_json  TEXT,
  updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subscription_entities_status
  ON subscription_entities(status);

CREATE TABLE IF NOT EXISTS subscription_charges (
  finance_transaction_id TEXT PRIMARY KEY,
  subscription_id        TEXT NOT NULL,
  date                   TEXT NOT NULL,
  merchant               TEXT,
  amount                 REAL,
  series_key             TEXT,
  effective_category     TEXT,
  category_source        TEXT,
  match_reason           TEXT,
  FOREIGN KEY(subscription_id) REFERENCES subscription_entities(subscription_id)
);

CREATE INDEX IF NOT EXISTS idx_subscription_charges_subscription
  ON subscription_charges(subscription_id, date);

CREATE TABLE IF NOT EXISTS subscription_usage_evidence (
  evidence_id      TEXT PRIMARY KEY,
  subscription_id  TEXT NOT NULL,
  source           TEXT NOT NULL,
  source_id        TEXT NOT NULL,
  started_at       TEXT NOT NULL,
  ended_at         TEXT,
  minutes          REAL NOT NULL DEFAULT 0,
  event_count      INTEGER NOT NULL DEFAULT 0,
  app_name         TEXT,
  bundle_id        TEXT,
  domain           TEXT,
  title            TEXT,
  confidence       REAL NOT NULL DEFAULT 0,
  reason           TEXT,
  FOREIGN KEY(subscription_id) REFERENCES subscription_entities(subscription_id)
);

CREATE INDEX IF NOT EXISTS idx_subscription_usage_subscription
  ON subscription_usage_evidence(subscription_id, started_at);
CREATE INDEX IF NOT EXISTS idx_subscription_usage_source
  ON subscription_usage_evidence(source, source_id);

CREATE TABLE IF NOT EXISTS subscription_utilization_periods (
  period_id          TEXT PRIMARY KEY,
  subscription_id    TEXT NOT NULL,
  period_start       TEXT NOT NULL,
  period_end         TEXT NOT NULL,
  cost               REAL NOT NULL DEFAULT 0,
  charge_count       INTEGER NOT NULL DEFAULT 0,
  usage_minutes      REAL NOT NULL DEFAULT 0,
  active_days        INTEGER NOT NULL DEFAULT 0,
  event_count        INTEGER NOT NULL DEFAULT 0,
  cost_per_hour      REAL,
  cost_per_active_day REAL,
  coverage_ratio     REAL NOT NULL DEFAULT 0,
  utilization_label  TEXT NOT NULL DEFAULT 'unknown',
  evidence_json      TEXT,
  computed_at        TEXT NOT NULL,
  FOREIGN KEY(subscription_id) REFERENCES subscription_entities(subscription_id)
);

CREATE INDEX IF NOT EXISTS idx_subscription_periods_subscription
  ON subscription_utilization_periods(subscription_id, period_start);

INSERT OR IGNORE INTO subscription_match_rules(
  merchant_pattern, label, domain_pattern, app_pattern, bundle_id, source
)
VALUES
  ('openai', 'OpenAI / ChatGPT', 'chatgpt.com', 'ChatGPT', NULL, 'system'),
  ('openai', 'OpenAI / ChatGPT', 'openai.com', NULL, NULL, 'system'),
  ('anthropic', 'Anthropic / Claude', 'claude.ai', 'Claude', NULL, 'system'),
  ('anthropic', 'Anthropic / Claude', 'claude.com', 'Claude', NULL, 'system'),
  ('claude', 'Anthropic / Claude', 'claude.ai', 'Claude', NULL, 'system'),
  ('cursor', 'Cursor', 'cursor.com', 'Cursor', 'com.todesktop.230313mzl4w4u92', 'system'),
  ('spotify', 'Spotify', 'spotify.com', 'Spotify', 'com.spotify.client', 'system'),
  ('netflix', 'Netflix', 'netflix.com', 'Netflix', 'com.netflix.Netflix', 'system'),
  ('youtube', 'YouTube Premium', 'youtube.com', 'YouTube', NULL, 'system'),
  ('apple', 'Apple', 'icloud.com', NULL, NULL, 'system'),
  ('apple.com/bill', 'Apple', 'icloud.com', NULL, NULL, 'system');
