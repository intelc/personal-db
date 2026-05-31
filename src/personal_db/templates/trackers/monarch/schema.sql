CREATE TABLE IF NOT EXISTS monarch_accounts (
  account_id                     TEXT PRIMARY KEY,
  display_name                   TEXT,
  mask                           TEXT,
  type_name                      TEXT,
  type_display                   TEXT,
  subtype_name                   TEXT,
  subtype_display                TEXT,
  institution_id                 TEXT,
  institution_name               TEXT,
  credential_id                  TEXT,
  data_provider                  TEXT,
  data_provider_account_id       TEXT,
  current_balance                REAL,
  display_balance                REAL,
  include_in_net_worth           INTEGER,
  include_balance_in_net_worth   INTEGER,
  hide_from_list                 INTEGER,
  hide_transactions_from_reports INTEGER,
  is_hidden                      INTEGER,
  is_asset                       INTEGER,
  is_manual                      INTEGER,
  sync_disabled                  INTEGER,
  transactions_count             INTEGER,
  holdings_count                 INTEGER,
  display_last_updated_at        TEXT,
  updated_at                     TEXT NOT NULL,
  raw_json                       TEXT
);

CREATE INDEX IF NOT EXISTS idx_monarch_accounts_type ON monarch_accounts(type_name);
CREATE INDEX IF NOT EXISTS idx_monarch_accounts_institution ON monarch_accounts(institution_name);

CREATE TABLE IF NOT EXISTS monarch_transactions (
  transaction_id    TEXT PRIMARY KEY,
  account_id        TEXT,
  account_name      TEXT,
  date              TEXT,
  amount            REAL,
  pending           INTEGER,
  merchant_id       TEXT,
  merchant_name     TEXT,
  category_id       TEXT,
  category_name     TEXT,
  hide_from_reports INTEGER,
  needs_review      INTEGER,
  review_status     TEXT,
  is_recurring      INTEGER,
  is_split          INTEGER,
  notes             TEXT,
  plaid_name        TEXT,
  created_at        TEXT,
  updated_at        TEXT,
  raw_json          TEXT
);

CREATE INDEX IF NOT EXISTS idx_monarch_transactions_date ON monarch_transactions(date);
CREATE INDEX IF NOT EXISTS idx_monarch_transactions_account ON monarch_transactions(account_id);

CREATE TABLE IF NOT EXISTS monarch_account_balances (
  balance_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  date       TEXT NOT NULL,
  balance    REAL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monarch_balances_account ON monarch_account_balances(account_id);
CREATE INDEX IF NOT EXISTS idx_monarch_balances_date ON monarch_account_balances(date);

CREATE TABLE IF NOT EXISTS monarch_holdings (
  holding_id     TEXT PRIMARY KEY,
  account_id     TEXT NOT NULL,
  security_id    TEXT,
  security_name  TEXT,
  ticker         TEXT,
  type           TEXT,
  quantity       REAL,
  basis          REAL,
  total_value    REAL,
  closing_price  REAL,
  current_price  REAL,
  last_synced_at TEXT,
  updated_at     TEXT NOT NULL,
  raw_json       TEXT
);

CREATE INDEX IF NOT EXISTS idx_monarch_holdings_account ON monarch_holdings(account_id);
CREATE INDEX IF NOT EXISTS idx_monarch_holdings_ticker ON monarch_holdings(ticker);

CREATE TABLE IF NOT EXISTS monarch_holding_snapshots (
  snapshot_id    TEXT PRIMARY KEY,
  date           TEXT NOT NULL,
  holding_id     TEXT NOT NULL,
  account_id     TEXT NOT NULL,
  security_id    TEXT,
  security_name  TEXT,
  ticker         TEXT,
  type           TEXT,
  quantity       REAL,
  basis          REAL,
  total_value    REAL,
  closing_price  REAL,
  current_price  REAL,
  last_synced_at TEXT,
  fetched_at     TEXT NOT NULL,
  raw_json       TEXT
);

CREATE INDEX IF NOT EXISTS idx_monarch_holding_snapshots_date ON monarch_holding_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_monarch_holding_snapshots_account ON monarch_holding_snapshots(account_id);
CREATE INDEX IF NOT EXISTS idx_monarch_holding_snapshots_holding ON monarch_holding_snapshots(holding_id);

DROP VIEW IF EXISTS monarch_finance_holdings_export;
DROP VIEW IF EXISTS monarch_finance_holding_snapshots_export;
DROP VIEW IF EXISTS monarch_finance_transactions_export;
DROP VIEW IF EXISTS monarch_finance_accounts_export;

CREATE TABLE IF NOT EXISTS monarch_account_labels (
  account_id           TEXT PRIMARY KEY,
  label                TEXT,
  owner                TEXT NOT NULL DEFAULT 'self',
  account_group        TEXT NOT NULL DEFAULT 'other',
  include_in_net_worth INTEGER NOT NULL DEFAULT 1,
  parent_draw_source   INTEGER NOT NULL DEFAULT 0,
  updated_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monarch_account_labels_owner ON monarch_account_labels(owner);
CREATE INDEX IF NOT EXISTS idx_monarch_account_labels_group ON monarch_account_labels(account_group);

CREATE TABLE IF NOT EXISTS monarch_account_exports (
  account_id      TEXT PRIMARY KEY,
  export_enabled  INTEGER NOT NULL DEFAULT 0,
  updated_at      TEXT NOT NULL
);

DROP TABLE IF EXISTS monarch_account_exports_new;
CREATE TABLE monarch_account_exports_new (
  account_id      TEXT PRIMARY KEY,
  export_enabled  INTEGER NOT NULL DEFAULT 0,
  updated_at      TEXT NOT NULL
);

INSERT OR REPLACE INTO monarch_account_exports_new(account_id, export_enabled, updated_at)
SELECT account_id, export_enabled, COALESCE(updated_at, datetime('now'))
FROM monarch_account_exports
WHERE account_id IS NOT NULL;

DROP TABLE monarch_account_exports;
ALTER TABLE monarch_account_exports_new RENAME TO monarch_account_exports;

CREATE INDEX IF NOT EXISTS idx_monarch_exports_enabled ON monarch_account_exports(export_enabled);

DROP VIEW IF EXISTS monarch_finance_accounts_export;
CREATE VIEW monarch_finance_accounts_export AS
SELECT
  'monarch' AS source,
  a.account_id AS source_account_id,
  'monarch:' || a.account_id AS finance_account_id,
  COALESCE(NULLIF(TRIM(l.owner), ''), 'self') AS owner,
  CASE
    WHEN COALESCE(l.account_group, '') IN ('cash', 'credit_card', 'investments', 'other')
      THEN l.account_group
    ELSE 'other'
  END AS account_group,
  a.institution_name AS institution_name,
  a.display_name AS account_name,
  a.mask AS mask,
  COALESCE(a.type_name, a.type_display) AS type,
  COALESCE(a.subtype_name, a.subtype_display) AS subtype,
  COALESCE(a.current_balance, a.display_balance) AS current_balance,
  NULL AS available_balance,
  'USD' AS iso_currency_code,
  CASE
    WHEN LOWER(COALESCE(NULLIF(TRIM(l.owner), ''), 'self')) IN ('self', 'me', 'personal') THEN 1
    ELSE 0
  END AS include_in_net_worth,
  CASE
    WHEN LOWER(COALESCE(NULLIF(TRIM(l.owner), ''), 'self')) IN ('self', 'me', 'personal') THEN 0
    ELSE 1
  END AS parent_draw_source,
  COALESCE(a.display_last_updated_at, a.updated_at) AS as_of,
  a.raw_json AS raw_json
FROM monarch_accounts a
JOIN monarch_account_exports e ON e.account_id = a.account_id
LEFT JOIN monarch_account_labels l ON l.account_id = a.account_id
WHERE COALESCE(e.export_enabled, 0) = 1
  AND a.account_id IS NOT NULL;

DROP VIEW IF EXISTS monarch_finance_transactions_export;
CREATE VIEW monarch_finance_transactions_export AS
SELECT
  'monarch' AS source,
  t.transaction_id AS source_transaction_id,
  'monarch:' || t.transaction_id AS finance_transaction_id,
  t.account_id AS source_account_id,
  a.finance_account_id AS finance_account_id,
  t.date AS date,
  COALESCE(t.plaid_name, t.merchant_name) AS name,
  t.merchant_name AS merchant_name,
  -COALESCE(t.amount, 0) AS amount,
  t.amount AS source_amount,
  COALESCE(t.pending, 0) AS pending,
  t.category_name AS category,
  CASE
    WHEN LOWER(COALESCE(t.category_name, '') || ' ' || COALESCE(t.merchant_name, '') || ' ' || COALESCE(t.plaid_name, '')) LIKE '%credit card payment%' THEN 1
    WHEN LOWER(COALESCE(t.category_name, '') || ' ' || COALESCE(t.merchant_name, '') || ' ' || COALESCE(t.plaid_name, '')) LIKE '%autopay%' THEN 1
    ELSE 0
  END AS is_credit_card_payment,
  CASE
    WHEN LOWER(COALESCE(t.category_name, '')) LIKE '%transfer%' THEN 1
    ELSE 0
  END AS is_internal_transfer,
  t.raw_json AS raw_json
FROM monarch_transactions t
JOIN monarch_finance_accounts_export a ON a.source_account_id = t.account_id
WHERE t.transaction_id IS NOT NULL
  AND COALESCE(t.hide_from_reports, 0) = 0;

DROP VIEW IF EXISTS monarch_finance_holdings_export;
CREATE VIEW monarch_finance_holdings_export AS
SELECT
  'monarch' AS source,
  h.holding_id AS source_holding_id,
  'monarch:' || h.holding_id AS finance_holding_id,
  h.account_id AS source_account_id,
  a.finance_account_id AS finance_account_id,
  h.security_id AS security_id,
  h.security_name AS security_name,
  h.ticker AS ticker,
  h.type AS type,
  h.quantity AS quantity,
  h.basis AS cost_basis,
  COALESCE(h.current_price, h.closing_price) AS price,
  h.total_value AS value,
  COALESCE(h.last_synced_at, h.updated_at) AS as_of,
  h.raw_json AS raw_json
FROM monarch_holdings h
JOIN monarch_finance_accounts_export a ON a.source_account_id = h.account_id
WHERE h.holding_id IS NOT NULL;

DROP VIEW IF EXISTS monarch_finance_holding_snapshots_export;
CREATE VIEW monarch_finance_holding_snapshots_export AS
SELECT
  'monarch' AS source,
  h.snapshot_id AS source_holding_snapshot_id,
  'monarch:' || h.snapshot_id AS finance_holding_snapshot_id,
  h.holding_id AS source_holding_id,
  'monarch:' || h.holding_id AS finance_holding_id,
  h.account_id AS source_account_id,
  a.finance_account_id AS finance_account_id,
  h.date AS date,
  h.security_id AS security_id,
  h.security_name AS security_name,
  h.ticker AS ticker,
  h.type AS type,
  h.quantity AS quantity,
  h.basis AS cost_basis,
  COALESCE(h.current_price, h.closing_price) AS price,
  h.total_value AS value,
  COALESCE(h.last_synced_at, h.fetched_at) AS as_of,
  h.raw_json AS raw_json
FROM monarch_holding_snapshots h
JOIN monarch_finance_accounts_export a ON a.source_account_id = h.account_id
WHERE h.snapshot_id IS NOT NULL;
