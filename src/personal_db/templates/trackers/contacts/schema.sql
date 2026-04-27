CREATE TABLE IF NOT EXISTS contacts (
  contact_id   TEXT PRIMARY KEY,
  full_name    TEXT,
  display_name TEXT,
  organization TEXT,
  source       TEXT,
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_handles (
  contact_id  TEXT NOT NULL,
  kind        TEXT NOT NULL,
  normalized  TEXT NOT NULL,
  raw         TEXT NOT NULL,
  PRIMARY KEY (contact_id, kind, normalized),
  FOREIGN KEY (contact_id) REFERENCES contacts(contact_id)
);

CREATE INDEX IF NOT EXISTS idx_contact_handles_normalized
  ON contact_handles(normalized);
CREATE INDEX IF NOT EXISTS idx_contacts_display_name
  ON contacts(display_name);
