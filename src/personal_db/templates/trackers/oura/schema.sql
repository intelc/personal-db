CREATE TABLE IF NOT EXISTS oura_daily_activity (
  id                          TEXT PRIMARY KEY,
  day                         TEXT NOT NULL,
  score                       INTEGER,
  active_calories             INTEGER,
  total_calories              INTEGER,
  steps                       INTEGER,
  equivalent_walking_distance INTEGER,
  high_activity_time          INTEGER,
  medium_activity_time        INTEGER,
  low_activity_time           INTEGER,
  sedentary_time              INTEGER,
  resting_time                INTEGER,
  non_wear_time               INTEGER,
  high_activity_met_minutes   INTEGER,
  medium_activity_met_minutes INTEGER,
  low_activity_met_minutes    INTEGER,
  inactivity_alerts           INTEGER,
  meters_to_target            INTEGER,
  target_calories             INTEGER,
  target_meters               INTEGER,
  average_met_minutes         REAL,
  timestamp                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_oura_daily_activity_day ON oura_daily_activity(day);

CREATE TABLE IF NOT EXISTS oura_daily_readiness (
  id                          TEXT PRIMARY KEY,
  day                         TEXT NOT NULL,
  score                       INTEGER,
  temperature_deviation       REAL,
  temperature_trend_deviation REAL,
  activity_balance            INTEGER,
  body_temperature            INTEGER,
  hrv_balance                 INTEGER,
  previous_day_activity       INTEGER,
  previous_night              INTEGER,
  recovery_index              INTEGER,
  resting_heart_rate          INTEGER,
  sleep_balance               INTEGER,
  timestamp                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_oura_daily_readiness_day ON oura_daily_readiness(day);

CREATE TABLE IF NOT EXISTS oura_daily_sleep (
  id                  TEXT PRIMARY KEY,
  day                 TEXT NOT NULL,
  score               INTEGER,
  contrib_deep_sleep  INTEGER,
  contrib_efficiency  INTEGER,
  contrib_latency     INTEGER,
  contrib_rem_sleep   INTEGER,
  contrib_restfulness INTEGER,
  contrib_timing      INTEGER,
  contrib_total_sleep INTEGER,
  timestamp           TEXT
);
CREATE INDEX IF NOT EXISTS idx_oura_daily_sleep_day ON oura_daily_sleep(day);

CREATE TABLE IF NOT EXISTS oura_daily_stress (
  id            TEXT PRIMARY KEY,
  day           TEXT NOT NULL,
  stress_high   INTEGER,
  recovery_high INTEGER,
  day_summary   TEXT
);
CREATE INDEX IF NOT EXISTS idx_oura_daily_stress_day ON oura_daily_stress(day);

CREATE TABLE IF NOT EXISTS oura_daily_spo2 (
  id                          TEXT PRIMARY KEY,
  day                         TEXT NOT NULL,
  spo2_percentage_avg         REAL,
  breathing_disturbance_index INTEGER
);
CREATE INDEX IF NOT EXISTS idx_oura_daily_spo2_day ON oura_daily_spo2(day);

CREATE TABLE IF NOT EXISTS oura_sleep (
  id                    TEXT PRIMARY KEY,
  day                   TEXT,
  bedtime_start         TEXT,
  bedtime_end           TEXT,
  type                  TEXT,
  period                INTEGER,
  total_sleep_duration  INTEGER,
  time_in_bed           INTEGER,
  awake_time            INTEGER,
  light_sleep_duration  INTEGER,
  deep_sleep_duration   INTEGER,
  rem_sleep_duration    INTEGER,
  efficiency            REAL,
  latency               INTEGER,
  restless_periods      INTEGER,
  average_breath        REAL,
  average_heart_rate    REAL,
  lowest_heart_rate     INTEGER,
  average_hrv           REAL,
  readiness_score_delta INTEGER,
  sleep_score_delta     INTEGER,
  low_battery_alert     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_oura_sleep_bedtime_start ON oura_sleep(bedtime_start);

CREATE TABLE IF NOT EXISTS oura_workout (
  id                 TEXT PRIMARY KEY,
  day                TEXT,
  start_datetime     TEXT,
  end_datetime       TEXT,
  activity           TEXT,
  intensity          TEXT,
  source             TEXT,
  load               REAL,
  average_heart_rate INTEGER,
  max_heart_rate     INTEGER,
  calories           REAL,
  distance           REAL,
  label              TEXT
);
CREATE INDEX IF NOT EXISTS idx_oura_workout_start ON oura_workout(start_datetime);

CREATE TABLE IF NOT EXISTS oura_session (
  id             TEXT PRIMARY KEY,
  day            TEXT,
  start_datetime TEXT,
  end_datetime   TEXT,
  type           TEXT,
  mood           TEXT
);
CREATE INDEX IF NOT EXISTS idx_oura_session_start ON oura_session(start_datetime);

CREATE TABLE IF NOT EXISTS oura_heartrate (
  timestamp TEXT NOT NULL,
  bpm       INTEGER,
  source    TEXT NOT NULL,
  PRIMARY KEY (timestamp, source)
);
CREATE INDEX IF NOT EXISTS idx_oura_heartrate_timestamp ON oura_heartrate(timestamp);
