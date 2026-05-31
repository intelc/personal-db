CREATE TABLE IF NOT EXISTS plaid_items (
  item_id                 TEXT PRIMARY KEY,
  institution_id          TEXT,
  institution_name        TEXT,
  webhook                 TEXT,
  products                TEXT,
  available_products      TEXT,
  billed_products         TEXT,
  consent_expiration_time TEXT,
  error_json              TEXT,
  created_at              TEXT,
  updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plaid_accounts (
  account_id               TEXT PRIMARY KEY,
  item_id                  TEXT NOT NULL,
  institution_name         TEXT,
  name                     TEXT,
  official_name            TEXT,
  mask                     TEXT,
  type                     TEXT,
  subtype                  TEXT,
  verification_status      TEXT,
  current_balance          REAL,
  available_balance        REAL,
  limit_balance            REAL,
  iso_currency_code        TEXT,
  unofficial_currency_code TEXT,
  balance_mode             TEXT,
  balance_as_of            TEXT NOT NULL,
  raw_json                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_plaid_accounts_item ON plaid_accounts(item_id);
CREATE INDEX IF NOT EXISTS idx_plaid_accounts_balance_as_of ON plaid_accounts(balance_as_of);

CREATE TABLE IF NOT EXISTS plaid_transactions (
  transaction_id              TEXT PRIMARY KEY,
  item_id                     TEXT NOT NULL,
  account_id                  TEXT NOT NULL,
  date                        TEXT,
  authorized_date             TEXT,
  datetime                    TEXT,
  authorized_datetime         TEXT,
  name                        TEXT,
  merchant_name               TEXT,
  amount                      REAL,
  iso_currency_code           TEXT,
  unofficial_currency_code    TEXT,
  pending                     INTEGER,
  pending_transaction_id      TEXT,
  payment_channel             TEXT,
  category                    TEXT,
  personal_finance_primary    TEXT,
  personal_finance_detailed   TEXT,
  personal_finance_confidence TEXT,
  check_number                TEXT,
  website                     TEXT,
  logo_url                    TEXT,
  removed_at                  TEXT,
  raw_json                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_plaid_transactions_date ON plaid_transactions(date);
CREATE INDEX IF NOT EXISTS idx_plaid_transactions_account ON plaid_transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_plaid_transactions_item ON plaid_transactions(item_id);

CREATE TABLE IF NOT EXISTS plaid_investment_holdings (
  snapshot_id                TEXT PRIMARY KEY,
  item_id                    TEXT NOT NULL,
  account_id                 TEXT NOT NULL,
  security_id                TEXT,
  as_of                      TEXT NOT NULL,
  quantity                   REAL,
  cost_basis                 REAL,
  institution_price          REAL,
  institution_price_as_of    TEXT,
  institution_price_datetime TEXT,
  institution_value          REAL,
  iso_currency_code          TEXT,
  unofficial_currency_code   TEXT,
  raw_json                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_plaid_holdings_as_of ON plaid_investment_holdings(as_of);
CREATE INDEX IF NOT EXISTS idx_plaid_holdings_account ON plaid_investment_holdings(account_id);
CREATE INDEX IF NOT EXISTS idx_plaid_holdings_security ON plaid_investment_holdings(security_id);

CREATE TABLE IF NOT EXISTS plaid_investment_securities (
  security_id              TEXT PRIMARY KEY,
  name                     TEXT,
  ticker_symbol            TEXT,
  type                     TEXT,
  subtype                  TEXT,
  close_price              REAL,
  close_price_as_of        TEXT,
  iso_currency_code        TEXT,
  unofficial_currency_code TEXT,
  raw_json                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_plaid_securities_ticker ON plaid_investment_securities(ticker_symbol);

CREATE TABLE IF NOT EXISTS plaid_investment_transactions (
  investment_transaction_id TEXT PRIMARY KEY,
  item_id                   TEXT NOT NULL,
  account_id                TEXT NOT NULL,
  security_id               TEXT,
  date                      TEXT NOT NULL,
  name                      TEXT,
  type                      TEXT,
  subtype                   TEXT,
  amount                    REAL,
  quantity                  REAL,
  price                     REAL,
  fees                      REAL,
  iso_currency_code         TEXT,
  unofficial_currency_code  TEXT,
  cancel_transaction_id     TEXT,
  raw_json                  TEXT
);

CREATE INDEX IF NOT EXISTS idx_plaid_investment_transactions_date ON plaid_investment_transactions(date);
CREATE INDEX IF NOT EXISTS idx_plaid_investment_transactions_account ON plaid_investment_transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_plaid_investment_transactions_security ON plaid_investment_transactions(security_id);

CREATE TABLE IF NOT EXISTS plaid_account_labels (
  account_id           TEXT PRIMARY KEY,
  owner                TEXT NOT NULL DEFAULT 'self',
  account_group        TEXT NOT NULL DEFAULT 'other',
  label                TEXT,
  include_in_net_worth INTEGER NOT NULL DEFAULT 1,
  parent_draw_source   INTEGER NOT NULL DEFAULT 0,
  notes                TEXT,
  updated_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plaid_account_labels_owner ON plaid_account_labels(owner);
CREATE INDEX IF NOT EXISTS idx_plaid_account_labels_group ON plaid_account_labels(account_group);

CREATE TABLE IF NOT EXISTS plaid_account_exports (
  account_id      TEXT PRIMARY KEY,
  export_enabled INTEGER NOT NULL DEFAULT 1,
  updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plaid_account_exports_enabled ON plaid_account_exports(export_enabled);

CREATE TABLE IF NOT EXISTS plaid_account_snapshots (
  snapshot_id       TEXT PRIMARY KEY,
  date              TEXT NOT NULL,
  account_id        TEXT NOT NULL,
  owner             TEXT NOT NULL,
  account_group     TEXT NOT NULL,
  institution_name  TEXT,
  account_name      TEXT,
  balance           REAL NOT NULL DEFAULT 0,
  net_worth_value   REAL NOT NULL DEFAULT 0,
  debt_value        REAL NOT NULL DEFAULT 0,
  iso_currency_code TEXT,
  as_of             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plaid_account_snapshots_date ON plaid_account_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_plaid_account_snapshots_owner ON plaid_account_snapshots(owner);
CREATE INDEX IF NOT EXISTS idx_plaid_account_snapshots_group ON plaid_account_snapshots(account_group);

CREATE TABLE IF NOT EXISTS plaid_daily_cashflow (
  date                 TEXT NOT NULL,
  owner                TEXT NOT NULL,
  income               REAL NOT NULL DEFAULT 0,
  spending             REAL NOT NULL DEFAULT 0,
  net                  REAL NOT NULL DEFAULT 0,
  parent_draw          REAL NOT NULL DEFAULT 0,
  credit_card_payments REAL NOT NULL DEFAULT 0,
  internal_transfers   REAL NOT NULL DEFAULT 0,
  txn_count            INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(date, owner)
);

CREATE INDEX IF NOT EXISTS idx_plaid_daily_cashflow_owner ON plaid_daily_cashflow(owner);

CREATE TABLE IF NOT EXISTS plaid_daily_net_worth (
  date             TEXT NOT NULL,
  owner            TEXT NOT NULL,
  cash             REAL NOT NULL DEFAULT 0,
  investments      REAL NOT NULL DEFAULT 0,
  credit_card_debt REAL NOT NULL DEFAULT 0,
  other            REAL NOT NULL DEFAULT 0,
  assets           REAL NOT NULL DEFAULT 0,
  debts            REAL NOT NULL DEFAULT 0,
  net_worth        REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(date, owner)
);

CREATE INDEX IF NOT EXISTS idx_plaid_daily_net_worth_owner ON plaid_daily_net_worth(owner);

CREATE TABLE IF NOT EXISTS plaid_parent_draws (
  transaction_id TEXT PRIMARY KEY,
  date           TEXT NOT NULL,
  owner          TEXT NOT NULL,
  account_id     TEXT NOT NULL,
  institution    TEXT,
  account_name   TEXT,
  merchant_name  TEXT,
  name           TEXT,
  amount         REAL NOT NULL,
  category       TEXT
);

CREATE INDEX IF NOT EXISTS idx_plaid_parent_draws_date ON plaid_parent_draws(date);
CREATE INDEX IF NOT EXISTS idx_plaid_parent_draws_owner ON plaid_parent_draws(owner);

DROP VIEW IF EXISTS plaid_finance_accounts_export;
CREATE VIEW plaid_finance_accounts_export AS
SELECT
  'plaid' AS source,
  a.account_id AS source_account_id,
  'plaid:' || a.account_id AS finance_account_id,
  COALESCE(NULLIF(TRIM(l.owner), ''), 'self') AS owner,
  CASE
    WHEN COALESCE(l.account_group, '') IN ('cash', 'credit_card', 'investments', 'other')
      THEN l.account_group
    WHEN LOWER(COALESCE(a.type, '')) = 'investment' THEN 'investments'
    WHEN LOWER(COALESCE(a.type, '')) = 'credit' THEN 'credit_card'
    WHEN LOWER(COALESCE(a.type, '')) = 'depository'
      AND LOWER(COALESCE(a.subtype, '')) IN ('checking', 'savings', 'money market', 'cash management', 'prepaid')
      THEN 'cash'
    ELSE 'other'
  END AS account_group,
  a.institution_name AS institution_name,
  COALESCE(l.label, a.official_name, a.name) AS account_name,
  a.mask AS mask,
  a.type AS type,
  a.subtype AS subtype,
  a.current_balance AS current_balance,
  a.available_balance AS available_balance,
  a.iso_currency_code AS iso_currency_code,
  CASE
    WHEN LOWER(COALESCE(NULLIF(TRIM(l.owner), ''), 'self')) IN ('self', 'me', 'personal') THEN 1
    ELSE 0
  END AS include_in_net_worth,
  CASE
    WHEN LOWER(COALESCE(NULLIF(TRIM(l.owner), ''), 'self')) IN ('self', 'me', 'personal') THEN 0
    ELSE 1
  END AS parent_draw_source,
  a.balance_as_of AS as_of,
  a.raw_json AS raw_json
FROM plaid_accounts a
LEFT JOIN plaid_account_labels l ON l.account_id = a.account_id
LEFT JOIN plaid_account_exports e ON e.account_id = a.account_id
WHERE a.account_id IS NOT NULL
  AND COALESCE(e.export_enabled, 1) = 1;

DROP VIEW IF EXISTS plaid_finance_transactions_export;
CREATE VIEW plaid_finance_transactions_export AS
SELECT
  'plaid' AS source,
  t.transaction_id AS source_transaction_id,
  'plaid:' || t.transaction_id AS finance_transaction_id,
  t.account_id AS source_account_id,
  'plaid:' || t.account_id AS finance_account_id,
  t.date AS date,
  t.name AS name,
  t.merchant_name AS merchant_name,
  t.amount AS amount,
  t.amount AS source_amount,
  COALESCE(t.pending, 0) AS pending,
  COALESCE(t.personal_finance_detailed, t.personal_finance_primary) AS category,
  CASE
    WHEN UPPER(COALESCE(t.personal_finance_detailed, '')) LIKE '%CREDIT_CARD_PAYMENT%' THEN 1
    WHEN UPPER(COALESCE(t.personal_finance_primary, '')) = 'LOAN_PAYMENTS'
      AND UPPER(COALESCE(t.name, '') || ' ' || COALESCE(t.merchant_name, '')) LIKE '%PAYMENT%' THEN 1
    WHEN UPPER(COALESCE(t.personal_finance_primary, '')) = 'LOAN_PAYMENTS'
      AND UPPER(COALESCE(t.name, '') || ' ' || COALESCE(t.merchant_name, '')) LIKE '%AUTOPAY%' THEN 1
    ELSE 0
  END AS is_credit_card_payment,
  CASE
    WHEN UPPER(COALESCE(t.personal_finance_primary, '')) LIKE 'TRANSFER_%' THEN 1
    WHEN UPPER(COALESCE(t.personal_finance_detailed, '')) LIKE 'TRANSFER_%' THEN 1
    ELSE 0
  END AS is_internal_transfer,
  t.raw_json AS raw_json
FROM plaid_transactions t
JOIN plaid_finance_accounts_export a ON a.source_account_id = t.account_id
WHERE t.transaction_id IS NOT NULL
  AND t.removed_at IS NULL;

DROP VIEW IF EXISTS plaid_finance_holdings_export;
CREATE VIEW plaid_finance_holdings_export AS
WITH ranked AS (
  SELECT
    h.*,
    ROW_NUMBER() OVER (
      PARTITION BY h.account_id, COALESCE(h.security_id, 'cash')
      ORDER BY h.as_of DESC, h.snapshot_id DESC
    ) AS rn
  FROM plaid_investment_holdings h
)
SELECT
  'plaid' AS source,
  h.account_id || ':' || COALESCE(h.security_id, 'cash') AS source_holding_id,
  'plaid:' || h.account_id || ':' || COALESCE(h.security_id, 'cash') AS finance_holding_id,
  h.account_id AS source_account_id,
  'plaid:' || h.account_id AS finance_account_id,
  h.security_id AS security_id,
  COALESCE(s.name, h.security_id) AS security_name,
  s.ticker_symbol AS ticker,
  COALESCE(s.type, s.subtype) AS type,
  h.quantity AS quantity,
  h.cost_basis AS cost_basis,
  h.institution_price AS price,
  h.institution_value AS value,
  h.as_of AS as_of,
  h.raw_json AS raw_json
FROM ranked h
JOIN plaid_finance_accounts_export a ON a.source_account_id = h.account_id
LEFT JOIN plaid_investment_securities s ON s.security_id = h.security_id
WHERE h.snapshot_id IS NOT NULL
  AND h.rn = 1;

DROP VIEW IF EXISTS plaid_finance_holding_snapshots_export;
CREATE VIEW plaid_finance_holding_snapshots_export AS
SELECT
  'plaid' AS source,
  h.snapshot_id AS source_holding_snapshot_id,
  'plaid:' || h.snapshot_id AS finance_holding_snapshot_id,
  h.account_id || ':' || COALESCE(h.security_id, 'cash') AS source_holding_id,
  'plaid:' || h.account_id || ':' || COALESCE(h.security_id, 'cash') AS finance_holding_id,
  h.account_id AS source_account_id,
  'plaid:' || h.account_id AS finance_account_id,
  substr(h.as_of, 1, 10) AS date,
  h.security_id AS security_id,
  COALESCE(s.name, h.security_id) AS security_name,
  s.ticker_symbol AS ticker,
  COALESCE(s.type, s.subtype) AS type,
  h.quantity AS quantity,
  h.cost_basis AS cost_basis,
  h.institution_price AS price,
  h.institution_value AS value,
  h.as_of AS as_of,
  h.raw_json AS raw_json
FROM plaid_investment_holdings h
JOIN plaid_finance_accounts_export a ON a.source_account_id = h.account_id
LEFT JOIN plaid_investment_securities s ON s.security_id = h.security_id
WHERE h.snapshot_id IS NOT NULL;
