"""Central schema-migration engine.

Two independent tools live here:

1. ``ensure_columns`` -- the idempotent "add this column if missing" helper
   that used to be copy-pasted (with `PRAGMA table_info` + `ALTER TABLE ADD
   COLUMN` boilerplate) across the enrichment queue and half a dozen tracker
   templates. It only patches columns onto a table that already exists;
   tables themselves are always created by `schema.sql`.

2. ``apply_pending_migrations`` -- the per-tracker schema-version stamp +
   ordered migration-file runner. A bundled tracker/app that needs to reshape
   an existing column set (tighten a constraint, rebuild a table, backfill a
   derived column) bumps `schema_version` in its manifest and drops a numbered
   SQL file under an optional `migrations/` directory next to `schema.sql`.

   Each file is named ``NNN_description.sql`` where NNN is the *target*
   version that file brings the tracker to (matching `Manifest.schema_version`
   once all pending files have run). `apply_pending_migrations` runs every
   file whose NNN falls in ``(current_stamp, target_version]``, in ascending
   order, inside a single transaction, then stamps the new version. If any
   statement in any file raises, the whole transaction -- every migration file
   that ran earlier in this same call, plus the stamp update -- rolls back;
   nothing partial is left committed. `schema.sql` always runs after
   migrations (as it always has) and must stay a safe no-op for tables the
   migrations already brought up to date -- `CREATE TABLE IF NOT EXISTS` /
   `CREATE INDEX IF NOT EXISTS` are idempotent by construction, so bundled
   schema.sql files can (and do) declare the *end-state* shape of a table
   without re-describing the migration that got an existing install there.

   A tracker with no `migrations/` directory (the common case -- schema_version
   stays at the default of 1) just never has this fire: `current >= target`
   short-circuits before touching the database.

   A tracker's schema-version stamp is missing until the first time this
   function runs against it. A missing stamp is treated as version 1 (the
   implicit baseline every manifest starts at via `schema_version: int = 1`),
   *not* version 0 -- this matters for a from-scratch install: `schema.sql`
   alone already creates tables in their current (target) shape, so a
   migration file is only meaningful for reshaping a table that predates it.
   Migration authors should write files that are also safe to run against a
   table `schema.sql` just created fresh (see
   `templates/trackers/monarch/migrations/002_account_exports_rebuild.sql` for
   the pattern: `CREATE TABLE IF NOT EXISTS` + an idempotent rebuild-via-rename
   dance that is a no-op on an empty, already-correct table).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from personal_db.core.config import Config
from personal_db.core.db import transaction
from personal_db.core.manifest import Manifest

_MIGRATION_FILE_RE = re.compile(r"^(\d+)_")


class MigrationError(RuntimeError):
    """Raised when a tracker migration file fails to apply. The enclosing
    transaction is rolled back before this propagates -- no partial state
    is left committed."""


def ensure_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """Idempotently add any of `columns` (name -> DDL type/constraint) that
    `table` is missing any of. No-op if `table` doesn't exist yet -- callers
    create tables via schema.sql; this only ever patches columns onto a table
    that's already there. Does not commit; the caller controls the
    transaction (matching every existing call site's pattern of batching
    several guards before a single `con.commit()`)."""
    existing = {row[1] for row in con.execute(f'PRAGMA table_info("{table}")').fetchall()}
    if not existing:
        return
    for name, decl in columns.items():
        if name not in existing:
            con.execute(f'ALTER TABLE "{table}" ADD COLUMN {name} {decl}')


def _get_stamp(con: sqlite3.Connection, tracker: str) -> int:
    row = con.execute(
        "SELECT version FROM tracker_schema_versions WHERE tracker = ?", (tracker,)
    ).fetchone()
    return row[0] if row is not None else 1


def _set_stamp(con: sqlite3.Connection, tracker: str, version: int) -> None:
    con.execute(
        """
        INSERT INTO tracker_schema_versions(tracker, version, applied_at)
        VALUES (?, ?, ?)
        ON CONFLICT(tracker) DO UPDATE SET
            version = excluded.version,
            applied_at = excluded.applied_at
        """,
        (tracker, version, datetime.now(UTC).isoformat()),
    )


def _migration_files(migrations_dir: Path, *, after: int, through: int) -> list[Path]:
    if not migrations_dir.is_dir():
        return []
    numbered: list[tuple[int, Path]] = []
    for f in migrations_dir.iterdir():
        if not f.is_file() or f.suffix != ".sql":
            continue
        m = _MIGRATION_FILE_RE.match(f.name)
        if not m:
            continue
        version = int(m.group(1))
        if after < version <= through:
            numbered.append((version, f))
    numbered.sort(key=lambda pair: pair[0])
    return [f for _, f in numbered]


def _split_statements(sql: str) -> list[str]:
    """Split a migration file into individual statements so each can be run
    via `con.execute()` and therefore participate in the enclosing manual
    transaction (`con.executescript()` does not -- it implicitly commits
    before running and is not rolled back by `con.rollback()`, which would
    silently defeat the transactionality this module promises).

    This is intentionally not a general SQL parser: bundled migration files
    are authored by us, contain no semicolons inside string literals, and
    use `--` line comments only, which is all this needs to handle."""
    kept_lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    cleaned = "\n".join(kept_lines)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]


def apply_pending_migrations(
    cfg: Config, name: str, tracker_dir: Path, manifest: Manifest
) -> None:
    """Bring `name`'s schema-version stamp up to `manifest.schema_version`,
    running any `migrations/NNN_*.sql` files in between -- all inside one
    transaction, so a failure partway through leaves nothing committed (not
    even the migration files that ran before it in this same call). Safe to
    call on every sync/install: a no-op once the stamp has caught up.

    Callers still apply `schema.sql` afterward, exactly as before this
    function existed."""
    target = manifest.schema_version
    with transaction(cfg.db_path) as con:
        current = _get_stamp(con, name)
        if current >= target:
            return
        for migration_file in _migration_files(
            tracker_dir / "migrations", after=current, through=target
        ):
            try:
                for statement in _split_statements(migration_file.read_text()):
                    con.execute(statement)
            except sqlite3.Error as e:
                raise MigrationError(
                    f"{name}: migration {migration_file.name} failed: {e}"
                ) from e
        _set_stamp(con, name, target)
