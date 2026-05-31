CREATE TABLE IF NOT EXISTS finance_accounts (
  finance_account_id    TEXT PRIMARY KEY,
  source                TEXT NOT NULL,
  source_account_id     TEXT NOT NULL,
  owner                 TEXT NOT NULL DEFAULT 'self',
  account_group         TEXT NOT NULL DEFAULT 'other',
  institution_name      TEXT,
  account_name          TEXT,
  mask                  TEXT,
  type                  TEXT,
  subtype               TEXT,
  current_balance       REAL,
  available_balance     REAL,
  iso_currency_code     TEXT,
  include_in_net_worth  INTEGER NOT NULL DEFAULT 1,
  parent_draw_source    INTEGER NOT NULL DEFAULT 0,
  as_of                 TEXT NOT NULL,
  raw_json              TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_finance_accounts_source
  ON finance_accounts(source, source_account_id);
CREATE INDEX IF NOT EXISTS idx_finance_accounts_owner ON finance_accounts(owner);
CREATE INDEX IF NOT EXISTS idx_finance_accounts_group ON finance_accounts(account_group);

CREATE TABLE IF NOT EXISTS finance_transactions (
  finance_transaction_id TEXT PRIMARY KEY,
  source                 TEXT NOT NULL,
  source_transaction_id  TEXT NOT NULL,
  finance_account_id     TEXT NOT NULL,
  source_account_id      TEXT NOT NULL,
  date                   TEXT,
  name                   TEXT,
  merchant_name          TEXT,
  amount                 REAL,
  source_amount          REAL,
  pending                INTEGER NOT NULL DEFAULT 0,
  category               TEXT,
  owner                  TEXT NOT NULL DEFAULT 'self',
  account_group          TEXT NOT NULL DEFAULT 'other',
  is_credit_card_payment INTEGER NOT NULL DEFAULT 0,
  is_internal_transfer   INTEGER NOT NULL DEFAULT 0,
  parent_draw            REAL NOT NULL DEFAULT 0,
  raw_json               TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_finance_transactions_source
  ON finance_transactions(source, source_transaction_id);
CREATE INDEX IF NOT EXISTS idx_finance_transactions_date ON finance_transactions(date);
CREATE INDEX IF NOT EXISTS idx_finance_transactions_account ON finance_transactions(finance_account_id);
CREATE INDEX IF NOT EXISTS idx_finance_transactions_owner ON finance_transactions(owner);

CREATE TABLE IF NOT EXISTS finance_holdings (
  finance_holding_id  TEXT PRIMARY KEY,
  source              TEXT NOT NULL,
  source_holding_id   TEXT NOT NULL,
  finance_account_id  TEXT NOT NULL,
  source_account_id   TEXT NOT NULL,
  owner               TEXT NOT NULL DEFAULT 'self',
  account_group       TEXT NOT NULL DEFAULT 'investments',
  institution_name    TEXT,
  account_name        TEXT,
  security_id         TEXT,
  security_name       TEXT,
  ticker              TEXT,
  type                TEXT,
  quantity            REAL,
  cost_basis          REAL,
  price               REAL,
  value               REAL,
  as_of               TEXT,
  raw_json            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_finance_holdings_source
  ON finance_holdings(source, source_holding_id);
CREATE INDEX IF NOT EXISTS idx_finance_holdings_account ON finance_holdings(finance_account_id);
CREATE INDEX IF NOT EXISTS idx_finance_holdings_as_of ON finance_holdings(as_of);

CREATE TABLE IF NOT EXISTS finance_holding_snapshots (
  finance_holding_snapshot_id TEXT PRIMARY KEY,
  date                        TEXT NOT NULL,
  source                      TEXT NOT NULL,
  source_holding_snapshot_id  TEXT NOT NULL,
  finance_holding_id          TEXT NOT NULL,
  source_holding_id           TEXT NOT NULL,
  finance_account_id          TEXT NOT NULL,
  source_account_id           TEXT NOT NULL,
  owner                       TEXT NOT NULL DEFAULT 'self',
  account_group               TEXT NOT NULL DEFAULT 'investments',
  institution_name            TEXT,
  account_name                TEXT,
  security_id                 TEXT,
  security_name               TEXT,
  ticker                      TEXT,
  type                        TEXT,
  quantity                    REAL,
  cost_basis                  REAL,
  price                       REAL,
  value                       REAL,
  as_of                       TEXT,
  raw_json                    TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_finance_holding_snapshots_source
  ON finance_holding_snapshots(source, source_holding_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_finance_holding_snapshots_date ON finance_holding_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_finance_holding_snapshots_account ON finance_holding_snapshots(finance_account_id);
CREATE INDEX IF NOT EXISTS idx_finance_holding_snapshots_holding ON finance_holding_snapshots(finance_holding_id);

CREATE TABLE IF NOT EXISTS finance_account_snapshots (
  snapshot_id          TEXT PRIMARY KEY,
  date                 TEXT NOT NULL,
  finance_account_id   TEXT NOT NULL,
  source               TEXT NOT NULL,
  source_account_id    TEXT NOT NULL,
  owner                TEXT NOT NULL,
  account_group        TEXT NOT NULL,
  institution_name     TEXT,
  account_name         TEXT,
  balance              REAL NOT NULL DEFAULT 0,
  net_worth_value      REAL NOT NULL DEFAULT 0,
  debt_value           REAL NOT NULL DEFAULT 0,
  iso_currency_code    TEXT,
  include_in_net_worth INTEGER NOT NULL DEFAULT 1,
  as_of                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_finance_account_snapshots_date ON finance_account_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_finance_account_snapshots_owner ON finance_account_snapshots(owner);
CREATE INDEX IF NOT EXISTS idx_finance_account_snapshots_group ON finance_account_snapshots(account_group);

CREATE TABLE IF NOT EXISTS finance_daily_cashflow (
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

CREATE INDEX IF NOT EXISTS idx_finance_daily_cashflow_owner ON finance_daily_cashflow(owner);

CREATE TABLE IF NOT EXISTS finance_daily_net_worth (
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

CREATE INDEX IF NOT EXISTS idx_finance_daily_net_worth_owner ON finance_daily_net_worth(owner);

CREATE TABLE IF NOT EXISTS finance_parent_draws (
  finance_transaction_id TEXT PRIMARY KEY,
  source                 TEXT NOT NULL,
  source_transaction_id  TEXT NOT NULL,
  date                   TEXT NOT NULL,
  owner                  TEXT NOT NULL,
  finance_account_id     TEXT NOT NULL,
  source_account_id      TEXT NOT NULL,
  institution            TEXT,
  account_name           TEXT,
  merchant_name          TEXT,
  name                   TEXT,
  amount                 REAL NOT NULL,
  category               TEXT
);

CREATE INDEX IF NOT EXISTS idx_finance_parent_draws_date ON finance_parent_draws(date);
CREATE INDEX IF NOT EXISTS idx_finance_parent_draws_owner ON finance_parent_draws(owner);
