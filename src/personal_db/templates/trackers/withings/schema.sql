CREATE TABLE IF NOT EXISTS withings_measurements (
  grpid            TEXT PRIMARY KEY,
  date             TEXT NOT NULL,
  timezone         TEXT,
  attrib           INTEGER,
  category         INTEGER,
  device_id        TEXT,
  weight_kg        REAL,
  fat_ratio_pct    REAL,
  fat_mass_kg      REAL,
  lean_mass_kg     REAL,
  muscle_mass_kg   REAL,
  bone_mass_kg     REAL,
  hydration_kg     REAL,
  heart_pulse_bpm  INTEGER,
  created_at       TEXT,
  modified_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_withings_measurements_date ON withings_measurements(date);
CREATE INDEX IF NOT EXISTS idx_withings_measurements_modified_at ON withings_measurements(modified_at);
