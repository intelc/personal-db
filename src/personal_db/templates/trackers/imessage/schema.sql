CREATE TABLE IF NOT EXISTS imessage_messages (
  id          INTEGER PRIMARY KEY,
  person_id   INTEGER REFERENCES people(person_id),
  handle      TEXT,
  text        TEXT,
  is_from_me  INTEGER NOT NULL,
  sent_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_imessage_sent ON imessage_messages(sent_at);
CREATE INDEX IF NOT EXISTS idx_imessage_person ON imessage_messages(person_id);
