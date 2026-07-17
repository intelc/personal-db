-- Bring monarch_account_exports to schema_version 2: enforce
-- `updated_at NOT NULL` for installs that created the table before this
-- constraint existed (as an ALTER TABLE ... ADD COLUMN NOT NULL would fail
-- against SQLite without a DEFAULT, this is done via the standard
-- create-copy-drop-rename dance instead).
--
-- Safe to run against a fresh install too: CREATE TABLE IF NOT EXISTS is a
-- no-op if schema.sql already created the table in its (identical) target
-- shape, and the rebuild below is then just a no-op copy of an
-- already-correct, possibly-empty table.
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
