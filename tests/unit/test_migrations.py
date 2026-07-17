"""Tests for the central schema-migration engine: ensure_columns and the
per-tracker schema-version stamp + migration-file runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from personal_db.core.config import Config
from personal_db.core.db import connect, init_db
from personal_db.core.manifest import Manifest, load_manifest
from personal_db.core.migrations import (
    MigrationError,
    apply_pending_migrations,
    ensure_columns,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MONARCH_DIR = REPO_ROOT / "src/personal_db/templates/trackers/monarch"


# --------------------------------------------------------------------------
# ensure_columns
# --------------------------------------------------------------------------


def test_ensure_columns_adds_missing_column(tmp_path):
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
    ensure_columns(con, "t", {"new_col": "TEXT"})
    con.commit()
    cols = {row[1] for row in con.execute("PRAGMA table_info(t)").fetchall()}
    assert "new_col" in cols
    con.close()


def test_ensure_columns_is_idempotent(tmp_path):
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (id TEXT PRIMARY KEY, existing TEXT)")
    # Calling twice must not raise (ALTER TABLE ADD COLUMN on an existing
    # column would error if this weren't guarded).
    ensure_columns(con, "t", {"existing": "TEXT", "new_col": "INTEGER"})
    ensure_columns(con, "t", {"existing": "TEXT", "new_col": "INTEGER"})
    con.commit()
    cols = {row[1] for row in con.execute("PRAGMA table_info(t)").fetchall()}
    assert cols == {"id", "existing", "new_col"}
    con.close()


def test_ensure_columns_noop_when_table_missing(tmp_path):
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE other (id TEXT)")
    # Should not raise even though "missing_table" doesn't exist.
    ensure_columns(con, "missing_table", {"col": "TEXT"})
    con.commit()
    con.close()


# --------------------------------------------------------------------------
# apply_pending_migrations
# --------------------------------------------------------------------------


def _write_manifest(tracker_dir: Path, name: str, schema_version: int) -> Manifest:
    tracker_dir.mkdir(parents=True, exist_ok=True)
    (tracker_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test tracker",
                "permission_type": "none",
                "schema_version": schema_version,
                "time_column": "ts",
                "schema": {
                    "tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )
    return load_manifest(tracker_dir / "manifest.yaml")


def test_apply_pending_migrations_noop_with_no_migrations_dir(tmp_root):
    """The common case: schema_version stays at its default of 1, a missing
    stamp is *treated* as already-version-1, so `current >= target` is true
    immediately and nothing is written -- no stamp row, no migrations/ lookup.
    This keeps every ordinary sync (the vast majority of trackers, forever)
    from doing pointless writes to tracker_schema_versions."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    tracker_dir = tmp_root / "trackers" / "demo"
    manifest = _write_manifest(tracker_dir, "demo", schema_version=1)

    apply_pending_migrations(cfg, "demo", tracker_dir, manifest)

    con = connect(cfg.db_path)
    row = con.execute(
        "SELECT version FROM tracker_schema_versions WHERE tracker = ?", ("demo",)
    ).fetchone()
    con.close()
    assert row is None


def test_apply_pending_migrations_runs_files_in_order_and_stamps_version(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    tracker_dir = tmp_root / "trackers" / "demo"
    manifest = _write_manifest(tracker_dir, "demo", schema_version=3)

    migrations_dir = tracker_dir / "migrations"
    migrations_dir.mkdir()
    with connect(cfg.db_path) as con:
        con.execute("CREATE TABLE demo_marks (label TEXT)")
        con.commit()
    # Deliberately create files out of numeric-vs-lexical order (002 before
    # 003, but written to disk so a naive lexical sort would still work here
    # -- the point is the runner sorts by the leading integer, not filename).
    (migrations_dir / "003_third.sql").write_text(
        "INSERT INTO demo_marks(label) VALUES ('third');"
    )
    (migrations_dir / "002_second.sql").write_text(
        "INSERT INTO demo_marks(label) VALUES ('second');"
    )

    apply_pending_migrations(cfg, "demo", tracker_dir, manifest)

    con = connect(cfg.db_path)
    labels = [row[0] for row in con.execute("SELECT label FROM demo_marks ORDER BY rowid")]
    stamp = con.execute(
        "SELECT version FROM tracker_schema_versions WHERE tracker = ?", ("demo",)
    ).fetchone()
    con.close()
    assert labels == ["second", "third"]
    assert stamp == (3,)


def test_apply_pending_migrations_skips_files_outside_range(tmp_root):
    """A missing stamp defaults to version 1 (Manifest.schema_version's own
    default) -- a migration file numbered 001 (at or below that baseline)
    must not run, only files strictly greater than the current stamp."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    tracker_dir = tmp_root / "trackers" / "demo"
    manifest = _write_manifest(tracker_dir, "demo", schema_version=2)

    migrations_dir = tracker_dir / "migrations"
    migrations_dir.mkdir()
    with connect(cfg.db_path) as con:
        con.execute("CREATE TABLE demo_marks (label TEXT)")
        con.commit()
    (migrations_dir / "001_baseline.sql").write_text(
        "INSERT INTO demo_marks(label) VALUES ('baseline');"
    )
    (migrations_dir / "002_target.sql").write_text(
        "INSERT INTO demo_marks(label) VALUES ('target');"
    )

    apply_pending_migrations(cfg, "demo", tracker_dir, manifest)

    con = connect(cfg.db_path)
    labels = [row[0] for row in con.execute("SELECT label FROM demo_marks ORDER BY rowid")]
    con.close()
    assert labels == ["target"]


def test_apply_pending_migrations_is_noop_once_stamp_current(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    tracker_dir = tmp_root / "trackers" / "demo"
    manifest = _write_manifest(tracker_dir, "demo", schema_version=2)
    migrations_dir = tracker_dir / "migrations"
    migrations_dir.mkdir()
    with connect(cfg.db_path) as con:
        con.execute("CREATE TABLE demo_marks (label TEXT)")
        con.commit()
    (migrations_dir / "002_target.sql").write_text(
        "INSERT INTO demo_marks(label) VALUES ('target');"
    )

    apply_pending_migrations(cfg, "demo", tracker_dir, manifest)
    apply_pending_migrations(cfg, "demo", tracker_dir, manifest)  # second call: no-op

    con = connect(cfg.db_path)
    count = con.execute("SELECT COUNT(*) FROM demo_marks").fetchone()[0]
    con.close()
    assert count == 1  # not re-run


def test_apply_pending_migrations_rolls_back_whole_batch_on_failure(tmp_root):
    """Transactionality: if a later migration file fails, an earlier file's
    effects in the *same call* are rolled back too, and the version stamp is
    not advanced -- nothing partial is left committed."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    tracker_dir = tmp_root / "trackers" / "demo"
    manifest = _write_manifest(tracker_dir, "demo", schema_version=3)
    migrations_dir = tracker_dir / "migrations"
    migrations_dir.mkdir()
    with connect(cfg.db_path) as con:
        con.execute("CREATE TABLE demo_marks (label TEXT)")
        con.commit()
    (migrations_dir / "002_ok.sql").write_text(
        "INSERT INTO demo_marks(label) VALUES ('ok');"
    )
    (migrations_dir / "003_broken.sql").write_text(
        "INSERT INTO no_such_table(label) VALUES ('boom');"
    )

    with pytest.raises(MigrationError):
        apply_pending_migrations(cfg, "demo", tracker_dir, manifest)

    con = connect(cfg.db_path)
    count = con.execute("SELECT COUNT(*) FROM demo_marks").fetchone()[0]
    stamp = con.execute(
        "SELECT version FROM tracker_schema_versions WHERE tracker = ?", ("demo",)
    ).fetchone()
    con.close()
    # The 002 migration's INSERT ran before the 003 failure but must not have
    # survived the rollback.
    assert count == 0
    assert stamp is None


# --------------------------------------------------------------------------
# monarch end-state equivalence
# --------------------------------------------------------------------------


def _monarch_export_columns(con: sqlite3.Connection) -> list[tuple]:
    return [
        (row[1], row[2], row[3])  # name, type, notnull
        for row in con.execute("PRAGMA table_info(monarch_account_exports)").fetchall()
    ]


def test_monarch_fresh_install_matches_migrated_legacy_shape(tmp_root):
    """A brand-new install (schema.sql only, no pre-existing table) and an
    upgrade from the pre-schema_version-2 shape (migration runs against an
    existing table) must land on the identical monarch_account_exports shape."""
    schema_sql = (MONARCH_DIR / "schema.sql").read_text()
    manifest = load_manifest(MONARCH_DIR / "manifest.yaml")
    assert manifest.schema_version == 2

    # Fresh install: schema.sql only (no migrations needed -- nothing to
    # bring forward), matching what a from-scratch `tracker install` does.
    fresh_cfg = Config(root=tmp_root / "fresh")
    init_db(fresh_cfg.db_path)
    with connect(fresh_cfg.db_path) as con:
        con.executescript(schema_sql)
        con.commit()
    with connect(fresh_cfg.db_path) as con:
        fresh_columns = _monarch_export_columns(con)

    # Legacy install: simulate the old pre-rebuild table shape (updated_at
    # nullable, as it was before schema_version 2), then run the migration
    # engine the way sync_one/install would.
    legacy_cfg = Config(root=tmp_root / "legacy")
    init_db(legacy_cfg.db_path)
    with connect(legacy_cfg.db_path) as con:
        con.execute(
            """
            CREATE TABLE monarch_account_exports (
              account_id      TEXT PRIMARY KEY,
              export_enabled  INTEGER NOT NULL DEFAULT 0,
              updated_at      TEXT
            )
            """
        )
        con.execute(
            "INSERT INTO monarch_account_exports(account_id, export_enabled, updated_at) "
            "VALUES ('acct-1', 1, '2026-01-01T00:00:00+00:00')"
        )
        con.commit()

    apply_pending_migrations(legacy_cfg, "monarch", MONARCH_DIR, manifest)
    with connect(legacy_cfg.db_path) as con:
        con.executescript(schema_sql)
        con.commit()
    with connect(legacy_cfg.db_path) as con:
        legacy_columns = _monarch_export_columns(con)
        preserved = con.execute(
            "SELECT account_id, export_enabled, updated_at FROM monarch_account_exports"
        ).fetchall()

    assert fresh_columns == legacy_columns
    # updated_at is now NOT NULL in both -- confirm via table_info's notnull flag.
    updated_at_notnull = {name: notnull for name, _type, notnull in legacy_columns}
    assert updated_at_notnull["updated_at"] == 1
    # The rebuild preserved existing rows rather than discarding them.
    assert preserved == [("acct-1", 1, "2026-01-01T00:00:00+00:00")]

    # And a second application of schema.sql alone (as every sync does) is a
    # pure no-op on the now-current shape -- re-running it doesn't re-trigger
    # any rebuild dance since that lives only in the migration file now.
    with connect(legacy_cfg.db_path) as con:
        con.executescript(schema_sql)
        con.commit()
    with connect(legacy_cfg.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM monarch_account_exports").fetchone()[0] == 1
