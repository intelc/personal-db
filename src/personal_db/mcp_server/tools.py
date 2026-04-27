from __future__ import annotations

import py_compile
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import yaml

from personal_db.config import Config
from personal_db.db import connect
from personal_db.log_event import log_event
from personal_db.manifest import load_manifest
from personal_db.notes import list_notes, read_note

# Cap file writes to keep the tool from being a foot-gun. Real tracker files
# are well under 100 KB; 1 MiB leaves plenty of headroom.
_MAX_TRACKER_FILE_BYTES = 1 * 1024 * 1024
# Tracker name pattern: lowercase letters/digits/underscores, must start with a letter.
_TRACKER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Reject any write or schema-altering verb. Also block `;` to prevent stacked statements.
_WRITE_VERBS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM)\b",
    re.IGNORECASE,
)


def _validate_select(sql: str) -> None:
    if ";" in sql.rstrip(";").strip(";"):
        raise ValueError("multiple statements not allowed")
    if _WRITE_VERBS_RE.search(sql):
        raise ValueError("only SELECT queries allowed")
    head = sql.lstrip().lstrip("(").lstrip().upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("query must start with SELECT or WITH")


def list_trackers(cfg: Config) -> list[dict[str, str]]:
    if not cfg.trackers_dir.exists():
        return []
    out = []
    for d in sorted(cfg.trackers_dir.iterdir()):
        m = d / "manifest.yaml"
        if d.is_dir() and m.exists():
            man = load_manifest(m)
            out.append({"name": man.name, "description": man.description})
    return out


def describe_tracker(cfg: Config, name: str) -> dict[str, Any]:
    return load_manifest(cfg.trackers_dir / name / "manifest.yaml").model_dump()


def query(cfg: Config, sql: str, params: list | None = None) -> list[dict[str, Any]]:
    _validate_select(sql)
    con = connect(cfg.db_path, read_only=True)
    cur = con.execute(sql, params or [])
    cols = [c[0] for c in cur.description] if cur.description else []
    rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
    con.close()
    return rows


def get_series(
    cfg: Config,
    *,
    tracker: str,
    range_: str,
    granularity: str = "day",
    agg: str = "sum",
    value_column: str | None = None,
) -> list[dict[str, Any]]:
    """Time-bucketed series. range_ is 'YYYY-MM-DD/YYYY-MM-DD'.
    Uses a matching `views/<tracker>_<granularity>.sql` view if present, else GROUP BY."""
    manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
    table = tracker if tracker in manifest.schema.tables else next(iter(manifest.schema.tables))
    time_col = manifest.time_column
    start, end = range_.split("/")
    if granularity not in ("hour", "day", "week", "month"):
        raise ValueError(f"unsupported granularity: {granularity}")
    if agg not in ("sum", "avg", "count", "min", "max"):
        raise ValueError(f"unsupported agg: {agg}")
    expr_value = f"{agg}({value_column})" if value_column else "count(*)"
    fmt = {"hour": "%Y-%m-%dT%H", "day": "%Y-%m-%d", "week": "%Y-W%W", "month": "%Y-%m"}[
        granularity
    ]
    sql = (
        f"SELECT strftime('{fmt}', {time_col}) AS bucket, {expr_value} AS value "
        f"FROM {table} WHERE {time_col} >= ? AND {time_col} < ? "
        f"GROUP BY bucket ORDER BY bucket"
    )
    return query(cfg, sql, params=[start, end])


def list_entities(cfg: Config, kind: str, query_str: str | None = None) -> list[dict[str, Any]]:
    if kind not in ("people", "topics"):
        raise ValueError("kind must be 'people' or 'topics'")
    id_col = "person_id" if kind == "people" else "topic_id"
    con = connect(cfg.db_path, read_only=True)
    if query_str:
        sql = (
            f"SELECT e.{id_col} as id, e.display_name, "
            f"GROUP_CONCAT(a.alias) as aliases "
            f"FROM {kind} e LEFT JOIN {kind}_aliases a USING({id_col}) "
            f"WHERE e.display_name LIKE ? OR a.alias LIKE ? "
            f"GROUP BY e.{id_col}"
        )
        rows = con.execute(sql, (f"%{query_str}%", f"%{query_str}%")).fetchall()
    else:
        sql = (
            f"SELECT e.{id_col} as id, e.display_name, "
            f"GROUP_CONCAT(a.alias) as aliases "
            f"FROM {kind} e LEFT JOIN {kind}_aliases a USING({id_col}) "
            f"GROUP BY e.{id_col}"
        )
        rows = con.execute(sql).fetchall()
    con.close()
    return [
        {"id": r[0], "display_name": r[1], "aliases": (r[2].split(",") if r[2] else [])}
        for r in rows
    ]


def log_event_tool(cfg: Config, tracker: str, fields: dict) -> int:
    return log_event(cfg, tracker, fields)


def list_notes_tool(cfg: Config, query_str: str | None = None) -> list[dict]:
    return list_notes(cfg, query_str)


def read_note_tool(cfg: Config, path: str) -> str:
    return read_note(cfg, path)


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

    Returns: {"inserted": N, "dates": [...]}.
    """
    from datetime import date as date_t
    from datetime import datetime

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
    con = connect(cfg.db_path, read_only=False)
    try:
        cur = con.cursor()
        dates: list[str] = []
        d = start
        from datetime import timedelta

        while d <= end:
            cur.execute(
                "INSERT INTO life_context(date, state, note, logged_at) VALUES (?, ?, ?, ?)",
                (d.isoformat(), state, note, logged_at),
            )
            dates.append(d.isoformat())
            d += timedelta(days=1)
        con.commit()
    finally:
        con.close()
    return {"inserted": len(dates), "dates": dates}


# ---------- tracker scaffolding tools ----------
#
# These let an MCP client (e.g. Claude in the Mac app, sandboxed without
# filesystem access) author/edit tracker files inside <root>/trackers/.
# Read and write are scoped to that subtree only — no escape via .., absolute
# paths, or symlinks. Other personal_db state (.env, db.sqlite, status files)
# is NOT exposed.


def _resolve_tracker_path(cfg: Config, rel_path: str) -> Path:
    """Resolve a path relative to <trackers_dir>, refusing anything that escapes.

    Symlinks are followed by resolve(); any resolved path that lands outside
    the trackers dir is rejected. Empty/`.`/abs paths are also rejected.
    """
    if not rel_path or rel_path in (".", "/"):
        raise ValueError("path is required and must point to a file")
    p = Path(rel_path)
    if p.is_absolute():
        raise ValueError(f"path must be relative to trackers dir, got: {rel_path}")
    base = cfg.trackers_dir.resolve()
    target = (base / p).resolve()
    try:
        target.relative_to(base)
    except ValueError as e:
        raise ValueError(f"path escapes trackers dir: {rel_path}") from e
    return target


def read_tracker_file(cfg: Config, path: str) -> dict[str, Any]:
    """Read a UTF-8 text file under <trackers_dir>/. Path is relative.

    Returns {"path": <rel>, "content": <text>, "size": <bytes>}.
    """
    target = _resolve_tracker_path(cfg, path)
    if not target.exists():
        raise FileNotFoundError(f"no such file: {path}")
    if not target.is_file():
        raise ValueError(f"not a regular file: {path}")
    size = target.stat().st_size
    if size > _MAX_TRACKER_FILE_BYTES:
        raise ValueError(f"file too large to read ({size} bytes): {path}")
    return {"path": path, "content": target.read_text(), "size": size}


def write_tracker_file(cfg: Config, path: str, content: str) -> dict[str, Any]:
    """Create or overwrite a UTF-8 text file under <trackers_dir>/. Path is relative.

    Auto-creates parent directories (still subject to the same containment check).
    Returns {"path": <rel>, "bytes_written": N, "created": bool}.
    """
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_TRACKER_FILE_BYTES:
        raise ValueError(
            f"content too large ({len(encoded)} bytes; max {_MAX_TRACKER_FILE_BYTES})"
        )
    target = _resolve_tracker_path(cfg, path)
    created = not target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via tempfile + rename (so a crash mid-write doesn't truncate
    # the existing file). NamedTemporaryFile in the same dir guarantees rename
    # is atomic on the same filesystem.
    with tempfile.NamedTemporaryFile(
        "wb", dir=target.parent, prefix=".tmp.", delete=False
    ) as tmp:
        tmp.write(encoded)
        tmp_path = Path(tmp.name)
    tmp_path.replace(target)
    return {"path": path, "bytes_written": len(encoded), "created": created}


def validate_tracker(cfg: Config, name: str) -> dict[str, Any]:
    """Run lint checks on a tracker dir. Returns structured pass/fail per check.

    Checks:
      - manifest_yaml: yaml.safe_load parses
      - manifest_schema: load_manifest() (Pydantic) accepts the manifest
      - ingest_py: py_compile passes
      - schema_sql: executescript() runs against an in-memory sqlite

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

    def _check(name_: str, fn) -> None:
        try:
            detail = fn() or "ok"
            checks.append({"name": name_, "ok": True, "detail": detail})
        except Exception as e:  # noqa: BLE001 — we want every failure mode reported
            checks.append({"name": name_, "ok": False, "detail": f"{type(e).__name__}: {e}"})

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

    return {"name": name, "ok": all(c["ok"] for c in checks), "checks": checks}
