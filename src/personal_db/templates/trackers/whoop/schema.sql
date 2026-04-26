CREATE TABLE IF NOT EXISTS whoop_cycles (
  id                  TEXT PRIMARY KEY,
  start               TEXT NOT NULL,
  end                 TEXT,
  timezone_offset     TEXT,
  score_state         TEXT,
  strain              REAL,
  kilojoule           REAL,
  average_heart_rate  INTEGER,
  max_heart_rate      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_whoop_cycles_start ON whoop_cycles(start);

CREATE TABLE IF NOT EXISTS whoop_recovery (
  cycle_id            TEXT PRIMARY KEY,
  sleep_id            TEXT,
  start               TEXT,
  score_state         TEXT,
  recovery_score      INTEGER,
  resting_heart_rate  INTEGER,
  hrv_rmssd_milli     REAL,
  spo2_percentage     REAL,
  skin_temp_celsius   REAL
);
CREATE INDEX IF NOT EXISTS idx_whoop_recovery_start ON whoop_recovery(start);

CREATE TABLE IF NOT EXISTS whoop_sleep (
  id                          TEXT PRIMARY KEY,
  start                       TEXT,
  end                         TEXT,
  timezone_offset             TEXT,
  nap                         INTEGER,
  score_state                 TEXT,
  total_in_bed_milli          INTEGER,
  total_awake_milli           INTEGER,
  total_light_sleep_milli     INTEGER,
  total_slow_wave_sleep_milli INTEGER,
  total_rem_sleep_milli       INTEGER,
  sleep_cycle_count           INTEGER,
  disturbance_count           INTEGER,
  respiratory_rate            REAL,
  sleep_performance_pct       REAL,
  sleep_consistency_pct       REAL,
  sleep_efficiency_pct        REAL
);
CREATE INDEX IF NOT EXISTS idx_whoop_sleep_start ON whoop_sleep(start);

CREATE TABLE IF NOT EXISTS whoop_workouts (
  id                   TEXT PRIMARY KEY,
  start                TEXT,
  end                  TEXT,
  timezone_offset      TEXT,
  sport_id             INTEGER,
  score_state          TEXT,
  strain               REAL,
  average_heart_rate   INTEGER,
  max_heart_rate       INTEGER,
  kilojoule            REAL,
  percent_recorded     REAL,
  distance_meter       REAL,
  altitude_gain_meter  REAL,
  altitude_change_meter REAL,
  zone_zero_milli      INTEGER,
  zone_one_milli       INTEGER,
  zone_two_milli       INTEGER,
  zone_three_milli     INTEGER,
  zone_four_milli      INTEGER,
  zone_five_milli      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_whoop_workouts_start ON whoop_workouts(start);
