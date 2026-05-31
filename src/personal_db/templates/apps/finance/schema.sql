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

CREATE TABLE IF NOT EXISTS app_finance_burn_rules (
  rule_id             INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_key            TEXT NOT NULL UNIQUE,
  priority            INTEGER NOT NULL DEFAULT 1000,
  label               TEXT NOT NULL,
  bucket              TEXT NOT NULL,
  merchant_pattern    TEXT,
  category_pattern    TEXT,
  category_match_type TEXT NOT NULL DEFAULT 'contains',
  flag_name           TEXT,
  amount_direction    TEXT NOT NULL DEFAULT 'any',
  min_amount          REAL,
  reason              TEXT,
  source              TEXT NOT NULL DEFAULT 'user',
  enabled             INTEGER NOT NULL DEFAULT 1,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_app_finance_burn_rules_enabled_priority
  ON app_finance_burn_rules(enabled, priority);

CREATE TABLE IF NOT EXISTS app_finance_burn_overrides (
  finance_transaction_id TEXT PRIMARY KEY,
  bucket                 TEXT NOT NULL,
  note                   TEXT,
  updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_finance_burn_buckets (
  bucket     TEXT PRIMARY KEY,
  label      TEXT NOT NULL,
  emoji      TEXT,
  sort_order INTEGER NOT NULL DEFAULT 1000,
  source     TEXT NOT NULL DEFAULT 'user',
  color      TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO app_finance_burn_rules(
  rule_key, priority, label, bucket, merchant_pattern, category_pattern,
  category_match_type, flag_name, amount_direction, min_amount, reason, source
)
VALUES
  ('system:rent_reimbursement:curio', 5, 'Curio rent reimbursement', 'rent', 'curio', NULL, 'contains', NULL, 'negative', NULL, 'rent reimbursement', 'system'),
  ('system:rent_reimbursement:curiosity_research', 5, 'Curiosity Research rent reimbursement', 'rent', 'curiosity research', NULL, 'contains', NULL, 'negative', NULL, 'rent reimbursement', 'system'),
  ('system:rent_reimbursement:oliver_zou', 5, 'Oliver Zou rent reimbursement', 'rent', 'oliver zou', NULL, 'contains', NULL, 'negative', NULL, 'rent reimbursement', 'system'),
  ('system:exclude:internal_transfer', 10, 'Exclude internal transfers', 'exclude', NULL, NULL, 'contains', 'is_internal_transfer', 'any', NULL, 'excluded internal transfer', 'system'),
  ('system:exclude:credit_card_payment', 10, 'Exclude credit card payments', 'exclude', NULL, NULL, 'contains', 'is_credit_card_payment', 'any', NULL, 'excluded credit card payment', 'system'),
  ('system:rent:greystar_mortgage', 20, 'Greystar rent payment', 'rent', 'greystar', 'mortgage', 'contains', NULL, 'positive', 1000, 'rent payment', 'system'),
  ('system:rent:greystar_rent', 20, 'Greystar rent payment', 'rent', 'greystar', 'RENT_AND_UTILITIES', 'starts', NULL, 'positive', 1000, 'rent payment', 'system'),
  ('system:rent:bilt_card_housing', 20, 'Bilt card housing rent', 'rent', 'bilt card housing', NULL, 'contains', NULL, 'positive', NULL, 'rent payment', 'system'),
  ('system:rent:bilt_mortgage', 20, 'Bilt mortgage rent', 'rent', 'bilt', 'mortgage', 'contains', NULL, 'positive', 1000, 'rent payment', 'system'),
  ('system:category:food', 80, 'Food source category', 'food', NULL, 'FOOD_AND_DRINK', 'starts', NULL, 'positive', NULL, 'food category', 'system'),
  ('system:category:transportation', 80, 'Transportation source category', 'transportation', NULL, 'TRANSPORTATION', 'starts', NULL, 'positive', NULL, 'transport/travel category', 'system'),
  ('system:category:travel', 80, 'Travel source category', 'transportation', NULL, 'TRAVEL', 'starts', NULL, 'positive', NULL, 'transport/travel category', 'system'),
  ('system:category:medical', 80, 'Medical source category', 'health', NULL, 'MEDICAL', 'starts', NULL, 'positive', NULL, 'health merchant', 'system'),
  ('system:category:entertainment_tv', 80, 'Streaming source category', 'subscriptions', NULL, 'ENTERTAINMENT_TV_AND_MOVIES', 'exact', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:category:telephone', 80, 'Telephone subscription source category', 'subscriptions', NULL, 'RENT_AND_UTILITIES_TELEPHONE', 'exact', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:ai:about_x', 50, 'About.x AI merchant', 'ai', 'about.x', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:anthropic', 50, 'Anthropic AI merchant', 'ai', 'anthropic', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:benjaminfire', 50, 'Benjaminfire AI merchant', 'ai', 'benjaminfire', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:claude', 50, 'Claude AI merchant', 'ai', 'claude', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:cursor', 50, 'Cursor AI merchant', 'ai', 'cursor', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:elevenlabs', 50, 'ElevenLabs AI merchant', 'ai', 'elevenlabs', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:higgsfield', 50, 'Higgsfield AI merchant', 'ai', 'higgsfield', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:magicpatt', 50, 'Magicpatt AI merchant', 'ai', 'magicpatt', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:midjourney', 50, 'Midjourney AI merchant', 'ai', 'midjourney', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:grok_xai', 50, 'Grok xAI merchant', 'ai', 'grok xai', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:omi_based_hardware', 50, 'Omi Based Hardware AI merchant', 'ai', 'omi based hardware', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:openai', 50, 'OpenAI merchant', 'ai', 'openai', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:perplexity', 50, 'Perplexity AI merchant', 'ai', 'perplexity', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:replicate', 50, 'Replicate AI merchant', 'ai', 'replicate', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:runpod', 50, 'RunPod AI merchant', 'ai', 'runpod', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:usemotioca', 50, 'UseMotioca AI merchant', 'ai', 'usemotioca', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:x_ai', 50, 'xAI merchant', 'ai', 'x.ai', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:ai:xai', 50, 'xAI merchant', 'ai', 'xai', NULL, 'contains', NULL, 'positive', NULL, 'AI merchant', 'system'),
  ('system:health:labcorp', 50, 'LabCorp health merchant', 'health', 'labcorp', NULL, 'contains', NULL, 'positive', NULL, 'health merchant', 'system'),
  ('system:health:my_penn_medicine', 50, 'My Penn Medicine health merchant', 'health', 'my penn medicine', NULL, 'contains', NULL, 'positive', NULL, 'health merchant', 'system'),
  ('system:health:penn_medicine', 50, 'Penn Medicine health merchant', 'health', 'penn medicine', NULL, 'contains', NULL, 'positive', NULL, 'health merchant', 'system'),
  ('system:subscription:amazon_prime', 50, 'Amazon Prime subscription', 'subscriptions', 'amazon prime', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:apple', 50, 'Apple subscription', 'subscriptions', 'apple', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:apple_bill', 50, 'Apple bill subscription', 'subscriptions', 'apple.com/bill', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:cleverbridge', 50, 'Cleverbridge subscription', 'subscriptions', 'cleverbridge', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:cointracker', 50, 'CoinTracker subscription', 'subscriptions', 'cointracker', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:forfeit', 50, 'Forfeit subscription', 'subscriptions', 'forfeit', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:netflix', 50, 'Netflix subscription', 'subscriptions', 'netflix', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:onlyfans', 50, 'OnlyFans subscription', 'subscriptions', 'onlyfans', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:phtoshp', 50, 'Photoshop subscription', 'subscriptions', 'phtoshp', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:photoshop', 50, 'Photoshop subscription', 'subscriptions', 'photoshop', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:smugmug', 50, 'SmugMug subscription', 'subscriptions', 'smugmug', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:spotify', 50, 'Spotify subscription', 'subscriptions', 'spotify', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system'),
  ('system:subscription:youtube_premium', 50, 'YouTube Premium subscription', 'subscriptions', 'youtube premium', NULL, 'contains', NULL, 'positive', NULL, 'subscription pattern', 'system');
