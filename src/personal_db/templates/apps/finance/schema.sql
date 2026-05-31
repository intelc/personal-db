CREATE TABLE IF NOT EXISTS app_finance_reviews (
  review_key TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'reviewed',
  note       TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_app_finance_reviews_kind
  ON app_finance_reviews(kind);

CREATE TABLE IF NOT EXISTS app_finance_transaction_categories (
  finance_transaction_id TEXT PRIMARY KEY,
  category               TEXT NOT NULL,
  note                   TEXT,
  updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_app_finance_transaction_categories_category
  ON app_finance_transaction_categories(category);

CREATE TABLE IF NOT EXISTS app_finance_category_presets (
  category   TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO app_finance_category_presets(category)
VALUES
  ('Coffee'),
  ('Education'),
  ('Entertainment'),
  ('Family'),
  ('Fees'),
  ('Fitness'),
  ('Gifts'),
  ('Groceries'),
  ('Health'),
  ('Home'),
  ('Income'),
  ('Insurance'),
  ('Restaurants & Bars'),
  ('Shopping'),
  ('Subscriptions'),
  ('Taxes'),
  ('Transportation'),
  ('Travel'),
  ('Utilities'),
  ('Work'),
  ('Uncategorized');
