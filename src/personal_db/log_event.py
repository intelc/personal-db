from personal_db.config import Config
from personal_db.db import apply_tracker_schema, init_db, transaction
from personal_db.manifest import load_manifest


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
