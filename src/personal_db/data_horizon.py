"""Data horizon: per-tracker earliest-available-date metadata.

For trackers marked `local_only: true` in their manifest, the framework records
`min(time_column)` from the primary output table after each sync. This lets
derived trackers (and visualizations) distinguish "this day has zero recorded
activity" from "this day predates when we have any data at all" — useful when
local files have been wiped (system reinstall, app cache clear).

Storage: a single tracker_horizons table in db.sqlite. Computed and updated by
the sync runner; consumers read via `get(cfg, name)`.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from personal_db.config import Config
from personal_db.manifest import Manifest

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS tracker_horizons (
  tracker     TEXT PRIMARY KEY,
  horizon     TEXT NOT NULL,  -- ISO-8601 of min(time_column) on primary table
  computed_at TEXT NOT NULL   -- ISO-8601 of when we ran the query
)
"""


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(_TABLE_DDL)


def _primary_table(manifest: Manifest) -> str:
    """First table in manifest.schema.tables — by Pydantic insertion order this
    is the table the time_column refers to. Trackers with one table (most) are
    unambiguous; multi-table trackers (whoop) put the time-bearing one first."""
    return next(iter(manifest.schema.tables))


def compute_and_store(cfg: Config, name: str, manifest: Manifest) -> str | None:
    """If `local_only`, compute min(time_column) on the primary table and upsert.

    Returns the stored horizon (ISO string) or None if local_only is False / no
    rows yet. Failures are swallowed so a horizon-compute bug never breaks sync.
    """
    if not manifest.local_only:
        return None
    table = _primary_table(manifest)
    col = manifest.time_column
    con = sqlite3.connect(cfg.db_path)
    try:
        ensure_table(con)
        try:
            row = con.execute(f'SELECT MIN("{col}") FROM "{table}"').fetchone()
        except sqlite3.OperationalError:
            return None
        horizon = row[0] if row else None
        if not horizon:
            return None
        con.execute(
            "INSERT INTO tracker_horizons(tracker, horizon, computed_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tracker) DO UPDATE SET horizon=excluded.horizon, "
            "computed_at=excluded.computed_at",
            (name, horizon, datetime.now(UTC).isoformat()),
        )
        con.commit()
        return horizon
    finally:
        con.close()


def get(cfg: Config, name: str) -> str | None:
    """Fetch the stored horizon for a tracker, or None if unset / table missing."""
    con = sqlite3.connect(cfg.db_path)
    try:
        try:
            row = con.execute(
                "SELECT horizon FROM tracker_horizons WHERE tracker = ?", (name,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return row[0] if row else None
    finally:
        con.close()


def get_all(cfg: Config) -> dict[str, str]:
    """Return {tracker: horizon} for every tracker that has one."""
    con = sqlite3.connect(cfg.db_path)
    try:
        try:
            rows = con.execute("SELECT tracker, horizon FROM tracker_horizons").fetchall()
        except sqlite3.OperationalError:
            return {}
        return dict(rows)
    finally:
        con.close()
