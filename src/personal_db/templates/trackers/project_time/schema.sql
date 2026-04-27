CREATE TABLE IF NOT EXISTS project_time (
  date           TEXT    NOT NULL,
  project        TEXT    NOT NULL,
  hours          REAL    NOT NULL DEFAULT 0,
  commit_count   INTEGER NOT NULL DEFAULT 0,
  breakdown_json TEXT,
  PRIMARY KEY (date, project)
);

CREATE INDEX IF NOT EXISTS idx_project_time_date    ON project_time(date);
CREATE INDEX IF NOT EXISTS idx_project_time_project ON project_time(project);
