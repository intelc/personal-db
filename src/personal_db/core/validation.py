"""Tracker validation: lint checks plus the content-hash stamp that gates sync.

`sync_one`/`backfill_one` (core/sync.py) only trust a tracker's on-disk files
if they exactly match the files that were last validated — either via an
explicit `validate_tracker` call (MCP tool or `personal-db tracker validate`)
or, for bundled templates, automatically at install/reinstall time (core/
installer.py — bundled templates are pre-trusted). This turns "an agent
authored/edited a tracker, hopefully a human eyeballed ingest.py before it
ran against the live DB" from a social contract into something sync_one()
enforces mechanically: edit `ingest.py` by hand after validating, and the
next sync refuses to run until it's re-validated.

Hash scope: manifest.yaml, ingest.py, schema.sql, and any migrations/*.sql
—everything that determines what gets written to the database. Deliberately
NOT visualizations.py: that's presentation code that only reads already
-written rows to render HTML; it can't write anything, so drift there
doesn't need to re-gate sync.
"""

from __future__ import annotations

import hashlib
import py_compile
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from personal_db.core.config import Config
from personal_db.core.db import transaction
from personal_db.core.manifest import load_manifest

_TRACKER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Canonical files whose content determines what a tracker writes to the DB.
_HASHED_FILE_NAMES = ("manifest.yaml", "ingest.py", "schema.sql")


class TrackerNotValidatedError(RuntimeError):
    """Raised by sync when a tracker's files have no matching validation stamp."""


def _relative_hashed_paths(tracker_dir: Path) -> list[Path]:
    paths = [tracker_dir / name for name in _HASHED_FILE_NAMES if (tracker_dir / name).is_file()]
    migrations_dir = tracker_dir / "migrations"
    if migrations_dir.is_dir():
        paths.extend(p for p in migrations_dir.iterdir() if p.is_file())
    return sorted(paths, key=lambda p: p.relative_to(tracker_dir).as_posix())


def compute_files_hash(tracker_dir: Path) -> str:
    """sha256 over sorted relative-path + content of the hashed files (see module docstring)."""
    h = hashlib.sha256()
    for path in _relative_hashed_paths(tracker_dir):
        rel = path.relative_to(tracker_dir).as_posix()
        h.update(rel.encode())
        h.update(b":")
        h.update(path.read_bytes())
        h.update(b"\n")
    return h.hexdigest()


def _ensure_validation_table(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS tracker_validation ("
        "tracker TEXT PRIMARY KEY, files_sha256 TEXT NOT NULL, validated_at TEXT NOT NULL)"
    )


def record_validation(cfg: Config, name: str, files_hash: str) -> None:
    """Stamp `name` as validated against `files_hash`. Upserts (re-validating
    updates the existing row rather than erroring on the PRIMARY KEY)."""
    with transaction(cfg.db_path) as con:
        _ensure_validation_table(con)
        con.execute(
            "INSERT INTO tracker_validation (tracker, files_sha256, validated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(tracker) DO UPDATE SET "
            "files_sha256 = excluded.files_sha256, validated_at = excluded.validated_at",
            (name, files_hash, datetime.now(UTC).isoformat()),
        )


def is_validated(cfg: Config, name: str, tracker_dir: Path) -> bool:
    """True if `name`'s current on-disk files match its validation stamp."""
    with transaction(cfg.db_path) as con:
        _ensure_validation_table(con)
        row = con.execute(
            "SELECT files_sha256 FROM tracker_validation WHERE tracker = ?", (name,)
        ).fetchone()
    if row is None:
        return False
    return row[0] == compute_files_hash(tracker_dir)


def ensure_validated(cfg: Config, name: str, tracker_dir: Path) -> None:
    """Raise TrackerNotValidatedError unless `name`'s files match a stamp.

    Called by sync_one/backfill_one before any schema or ingest code runs.
    """
    if is_validated(cfg, name, tracker_dir):
        return
    raise TrackerNotValidatedError(
        f"tracker '{name}' files changed since last validation; run validate_tracker "
        f"(MCP) or `personal-db tracker validate {name}` before syncing"
    )


def validate_tracker(cfg: Config, name: str) -> dict[str, Any]:
    """Run lint checks on a tracker dir. Returns structured pass/fail per check.

    Checks:
      - manifest_yaml: yaml.safe_load parses
      - manifest_schema: load_manifest() (Pydantic) accepts the manifest
      - ingest_py: py_compile passes
      - schema_sql: executescript() runs against an in-memory sqlite

    On success (all checks pass), records a validation stamp so sync_one
    will accept this tracker's current files.

    Result shape:
      {"name": <name>, "ok": bool,
       "checks": [{"name": str, "ok": bool, "detail": str}, ...]}
    """
    if not _TRACKER_NAME_RE.match(name):
        raise ValueError(f"invalid tracker name: {name!r}")
    tdir = cfg.trackers_dir / name
    if not tdir.is_dir():
        raise FileNotFoundError(f"no such tracker: {name}")

    checks: list[dict[str, Any]] = []

    def _check(check_name: str, fn) -> None:
        try:
            detail = fn() or "ok"
            checks.append({"name": check_name, "ok": True, "detail": detail})
        except Exception as e:
            checks.append({"name": check_name, "ok": False, "detail": f"{type(e).__name__}: {e}"})

    manifest_path = tdir / "manifest.yaml"
    schema_path = tdir / "schema.sql"
    ingest_path = tdir / "ingest.py"

    def _yaml_check():
        if not manifest_path.is_file():
            raise FileNotFoundError("manifest.yaml missing")
        yaml.safe_load(manifest_path.read_text())
        return "manifest.yaml parses"

    def _manifest_check():
        load_manifest(manifest_path)
        return "manifest passes Pydantic schema"

    def _ingest_check():
        if not ingest_path.is_file():
            raise FileNotFoundError("ingest.py missing")
        py_compile.compile(str(ingest_path), doraise=True)
        return "ingest.py compiles"

    def _schema_check():
        if not schema_path.is_file():
            raise FileNotFoundError("schema.sql missing")
        sql = schema_path.read_text()
        con = sqlite3.connect(":memory:")
        try:
            con.executescript(sql)
        finally:
            con.close()
        return "schema.sql executes against in-memory sqlite"

    _check("manifest_yaml", _yaml_check)
    _check("manifest_schema", _manifest_check)
    _check("ingest_py", _ingest_check)
    _check("schema_sql", _schema_check)

    ok = all(c["ok"] for c in checks)
    if ok:
        record_validation(cfg, name, compute_files_hash(tdir))
    return {"name": name, "ok": ok, "checks": checks}
