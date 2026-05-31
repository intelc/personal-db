CREATE TABLE IF NOT EXISTS crypto_wallet_wallets (
  wallet_id             TEXT PRIMARY KEY,
  address               TEXT NOT NULL,
  label                 TEXT,
  chains                TEXT NOT NULL,
  owner                 TEXT NOT NULL DEFAULT 'self',
  account_group         TEXT NOT NULL DEFAULT 'investments',
  export_enabled        INTEGER NOT NULL DEFAULT 1,
  include_in_net_worth  INTEGER NOT NULL DEFAULT 1,
  parent_draw_source    INTEGER NOT NULL DEFAULT 0,
  total_networth_usd    REAL,
  native_balance_usd    REAL,
  token_balance_usd     REAL,
  holdings_value_usd    REAL,
  validation_status     TEXT NOT NULL DEFAULT 'unvalidated',
  validation_error      TEXT,
  last_validated_at     TEXT,
  updated_at            TEXT NOT NULL,
  raw_json              TEXT
);

CREATE INDEX IF NOT EXISTS idx_crypto_wallet_wallets_export ON crypto_wallet_wallets(export_enabled);
CREATE INDEX IF NOT EXISTS idx_crypto_wallet_wallets_owner ON crypto_wallet_wallets(owner);

CREATE TABLE IF NOT EXISTS crypto_wallet_token_balances (
  holding_id        TEXT PRIMARY KEY,
  wallet_id         TEXT NOT NULL,
  address           TEXT NOT NULL,
  chain             TEXT NOT NULL,
  block_number      TEXT,
  token_address     TEXT NOT NULL,
  native_token      INTEGER NOT NULL DEFAULT 0,
  name              TEXT,
  symbol            TEXT,
  decimals          INTEGER,
  balance_raw       TEXT,
  balance_formatted TEXT,
  quantity          REAL,
  usd_price         REAL,
  usd_value         REAL,
  possible_spam     INTEGER NOT NULL DEFAULT 0,
  verified_contract INTEGER NOT NULL DEFAULT 0,
  logo              TEXT,
  thumbnail         TEXT,
  fetched_at        TEXT NOT NULL,
  raw_json          TEXT
);

CREATE INDEX IF NOT EXISTS idx_crypto_wallet_token_balances_wallet ON crypto_wallet_token_balances(wallet_id);
CREATE INDEX IF NOT EXISTS idx_crypto_wallet_token_balances_chain ON crypto_wallet_token_balances(chain);
CREATE INDEX IF NOT EXISTS idx_crypto_wallet_token_balances_fetched ON crypto_wallet_token_balances(fetched_at);

CREATE TABLE IF NOT EXISTS crypto_wallet_token_balance_snapshots (
  snapshot_id       TEXT PRIMARY KEY,
  date              TEXT NOT NULL,
  holding_id        TEXT NOT NULL,
  wallet_id         TEXT NOT NULL,
  address           TEXT NOT NULL,
  chain             TEXT NOT NULL,
  block_number      TEXT,
  token_address     TEXT NOT NULL,
  native_token      INTEGER NOT NULL DEFAULT 0,
  name              TEXT,
  symbol            TEXT,
  decimals          INTEGER,
  balance_raw       TEXT,
  balance_formatted TEXT,
  quantity          REAL,
  usd_price         REAL,
  usd_value         REAL,
  possible_spam     INTEGER NOT NULL DEFAULT 0,
  verified_contract INTEGER NOT NULL DEFAULT 0,
  logo              TEXT,
  thumbnail         TEXT,
  fetched_at        TEXT NOT NULL,
  raw_json          TEXT
);

CREATE INDEX IF NOT EXISTS idx_crypto_wallet_token_balance_snapshots_date ON crypto_wallet_token_balance_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_crypto_wallet_token_balance_snapshots_wallet ON crypto_wallet_token_balance_snapshots(wallet_id);
CREATE INDEX IF NOT EXISTS idx_crypto_wallet_token_balance_snapshots_holding ON crypto_wallet_token_balance_snapshots(holding_id);

DROP VIEW IF EXISTS crypto_wallet_finance_accounts_export;
CREATE VIEW crypto_wallet_finance_accounts_export AS
SELECT
  'crypto_wallet' AS source,
  w.wallet_id AS source_account_id,
  'crypto_wallet:' || w.wallet_id AS finance_account_id,
  COALESCE(NULLIF(TRIM(w.owner), ''), 'self') AS owner,
  CASE
    WHEN COALESCE(w.account_group, '') IN ('cash', 'credit_card', 'investments', 'other')
      THEN w.account_group
    ELSE 'investments'
  END AS account_group,
  'Crypto Wallet' AS institution_name,
  COALESCE(NULLIF(TRIM(w.label), ''), substr(w.address, 1, 6) || '...' || substr(w.address, -4)) AS account_name,
  substr(w.address, -4) AS mask,
  CASE WHEN w.chains LIKE '%bitcoin%' THEN 'bitcoin_wallet' ELSE 'wallet' END AS type,
  CASE WHEN w.chains LIKE '%bitcoin%' THEN 'bitcoin' ELSE 'evm' END AS subtype,
  COALESCE(w.total_networth_usd, w.holdings_value_usd, 0) AS current_balance,
  NULL AS available_balance,
  'USD' AS iso_currency_code,
  COALESCE(w.include_in_net_worth, 1) AS include_in_net_worth,
  COALESCE(w.parent_draw_source, 0) AS parent_draw_source,
  COALESCE(w.last_validated_at, w.updated_at) AS as_of,
  w.raw_json AS raw_json
FROM crypto_wallet_wallets w
WHERE COALESCE(w.export_enabled, 1) = 1;

DROP VIEW IF EXISTS crypto_wallet_finance_holdings_export;
CREATE VIEW crypto_wallet_finance_holdings_export AS
SELECT
  'crypto_wallet' AS source,
  b.holding_id AS source_holding_id,
  'crypto_wallet:' || b.holding_id AS finance_holding_id,
  b.wallet_id AS source_account_id,
  'crypto_wallet:' || b.wallet_id AS finance_account_id,
  b.chain || ':' || b.token_address AS security_id,
  COALESCE(b.name, b.symbol, b.token_address) AS security_name,
  b.symbol AS ticker,
  CASE WHEN b.native_token = 1 THEN 'native_crypto' ELSE 'erc20' END AS type,
  b.quantity AS quantity,
  NULL AS cost_basis,
  b.usd_price AS price,
  b.usd_value AS value,
  b.fetched_at AS as_of,
  b.raw_json AS raw_json
FROM crypto_wallet_token_balances b
JOIN crypto_wallet_finance_accounts_export a ON a.source_account_id = b.wallet_id
WHERE COALESCE(b.usd_value, 0) != 0 OR COALESCE(b.quantity, 0) != 0;

DROP VIEW IF EXISTS crypto_wallet_finance_holding_snapshots_export;
CREATE VIEW crypto_wallet_finance_holding_snapshots_export AS
SELECT
  'crypto_wallet' AS source,
  b.snapshot_id AS source_holding_snapshot_id,
  'crypto_wallet:' || b.snapshot_id AS finance_holding_snapshot_id,
  b.holding_id AS source_holding_id,
  'crypto_wallet:' || b.holding_id AS finance_holding_id,
  b.wallet_id AS source_account_id,
  'crypto_wallet:' || b.wallet_id AS finance_account_id,
  b.date AS date,
  b.chain || ':' || b.token_address AS security_id,
  COALESCE(b.name, b.symbol, b.token_address) AS security_name,
  b.symbol AS ticker,
  CASE WHEN b.native_token = 1 THEN 'native_crypto' ELSE 'erc20' END AS type,
  b.quantity AS quantity,
  NULL AS cost_basis,
  b.usd_price AS price,
  b.usd_value AS value,
  b.fetched_at AS as_of,
  b.raw_json AS raw_json
FROM crypto_wallet_token_balance_snapshots b
JOIN crypto_wallet_finance_accounts_export a ON a.source_account_id = b.wallet_id
WHERE COALESCE(b.usd_value, 0) != 0 OR COALESCE(b.quantity, 0) != 0;
