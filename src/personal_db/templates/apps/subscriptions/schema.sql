CREATE TABLE IF NOT EXISTS ui_notes (
  note_id    TEXT PRIMARY KEY,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ui_note_refs (
  note_id       TEXT NOT NULL,
  ref           TEXT NOT NULL,
  ref_kind      TEXT,
  label         TEXT,
  metadata_json TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (note_id, ref),
  FOREIGN KEY(note_id) REFERENCES ui_notes(note_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ui_note_refs_ref
  ON ui_note_refs(ref);
