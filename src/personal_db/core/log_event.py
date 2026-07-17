from typing import Any

from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, init_db, transaction
from personal_db.core.manifest import load_manifest


def log_event(cfg: Config, tracker: str, fields: dict) -> int:
    tracker_dir = cfg.trackers_dir / tracker
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    # Ensure the DB and tracker table(s) exist before inserting.
    schema_sql_path = tracker_dir / "schema.sql"
    if schema_sql_path.exists():
        init_db(cfg.db_path)
        apply_tracker_schema(cfg.db_path, schema_sql_path.read_text())
    # find the primary table — by convention, the table whose name matches the tracker name,
    # else the first table in the manifest
    tables = manifest.schema.tables
    table_name = tracker if tracker in tables else next(iter(tables))
    declared = set(tables[table_name].columns.keys())
    extra = set(fields) - declared
    if extra:
        raise ValueError(f"unknown field(s) for {tracker}.{table_name}: {sorted(extra)}")
    cols = list(fields.keys())
    placeholders = ",".join("?" * len(cols))
    with transaction(cfg.db_path) as con:
        cur = con.execute(
            f"INSERT INTO {table_name} ({','.join(cols)}) VALUES ({placeholders})",
            tuple(fields[c] for c in cols),
        )
        rowid = cur.lastrowid
    return rowid


def log_life_context(
    cfg: Config,
    start_date: str,
    end_date: str | None = None,
    state: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Insert one or more rows into the life_context tracker.

    For ranges (end_date set and >= start_date), fans out one row per day with
    the same state/note. At least one of state/note must be provided.

    This is the single shared implementation behind the life_context tracker's
    declared `log_life_context` MCP tool (templates/trackers/life_context/
    tools.py) and the daemon's `/log_life_context` form route and the menubar
    quick-log handlers, which all call it directly since core.log_event has no
    dependency on services or the MCP dispatch machinery.

    Returns: {"inserted": N, "dates": [...]}.
    """
    from datetime import date as date_t
    from datetime import datetime, timedelta

    if not state and not note:
        raise ValueError("at least one of `state` or `note` is required")
    try:
        start = date_t.fromisoformat(start_date)
    except ValueError as e:
        raise ValueError(f"start_date must be YYYY-MM-DD: {e}") from e
    if end_date:
        try:
            end = date_t.fromisoformat(end_date)
        except ValueError as e:
            raise ValueError(f"end_date must be YYYY-MM-DD: {e}") from e
        if end < start:
            raise ValueError(f"end_date {end_date} is before start_date {start_date}")
    else:
        end = start

    logged_at = datetime.now().astimezone().isoformat()
    with transaction(cfg.db_path) as con:
        cur = con.cursor()
        dates: list[str] = []
        d = start
        while d <= end:
            cur.execute(
                "INSERT INTO life_context(date, state, note, logged_at) VALUES (?, ?, ?, ?)",
                (d.isoformat(), state, note, logged_at),
            )
            dates.append(d.isoformat())
            d += timedelta(days=1)
    return {"inserted": len(dates), "dates": dates}
