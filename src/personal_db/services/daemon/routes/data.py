"""Read-only per-tracker data browser: GET /api/v1/data/... + GET /t/<tracker>/data.

Answers "what has it collected about me" — a debugging/trust surface over the
tables a tracker's *installed* schema.sql declares. Every query here is
opened against the db in SQLite read-only mode (`file:...?mode=ro`, plus
`PRAGMA query_only = ON` for defense in depth) and every table name is
validated against `personal_db.core.sync.tracker_schema_tables()` — the same
whitelist the transform DAG validator uses — before it is ever interpolated
into SQL. Nothing here mutates the database.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from personal_db.core.config import Config
from personal_db.core.sync import tracker_schema_tables
from personal_db.services.daemon.routes.common import validate_name
from personal_db.ui.aggrid import grid as ag_grid
from personal_db.ui.components import empty_state

_MAX_STR_LEN = 4000
_DEFAULT_LIMIT = 100
_MIN_LIMIT = 1
_MAX_LIMIT = 500


def _quote_ident(name: str) -> str:
    """Double-quote a SQL identifier, escaping embedded quotes.

    `name` is expected to already be whitelist-validated against a tracker's
    schema tables (see `_installed_schema_tables`) before this is called --
    this is belt-and-suspenders, not the primary defense.
    """
    return '"' + name.replace('"', '""') + '"'


def _json_safe(value: Any) -> Any:
    """Coerce one SQLite column value into something `json.dumps` can serialize.

    Primitives pass through untouched (so numeric columns still sort/filter
    numerically in the grid); anything else (bytes, etc.) is stringified.
    Long strings are truncated with an ellipsis marker so one giant BLOB/TEXT
    cell can't blow up the response.
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    elif not isinstance(value, str):
        value = str(value)
    if len(value) > _MAX_STR_LEN:
        return value[:_MAX_STR_LEN] + "… [truncated]"
    return value


def _installed_schema_tables(cfg: Config, tracker: str) -> set[str] | None:
    """Tables declared by `tracker`'s *installed* schema.sql, or None if not installed.

    "Installed" means `<root>/trackers/<tracker>/manifest.yaml` exists (the
    installer's own definition -- see CLAUDE.md's installed-vs-template
    split). A tracker with no schema.sql (shouldn't normally happen) reports
    an empty table set rather than None -- it's installed, it just declares
    nothing to browse.
    """
    tracker_dir = cfg.trackers_dir / tracker
    if not (tracker_dir / "manifest.yaml").is_file():
        return None
    schema_path = tracker_dir / "schema.sql"
    if not schema_path.is_file():
        return set()
    return tracker_schema_tables(schema_path.read_text())


def _open_ro(cfg: Config) -> sqlite3.Connection | None:
    """Open the db read-only, or None if it doesn't exist yet (never synced)."""
    if not cfg.db_path.exists():
        return None
    con = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    return con


def _row_count(cfg: Config, table: str) -> int:
    """Row count for one whitelisted table. 0 if the db or table doesn't exist yet."""
    con = _open_ro(cfg)
    if con is None:
        return 0
    try:
        with contextlib.closing(con):
            (count,) = con.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()
            return int(count)
    except sqlite3.OperationalError:
        # Table declared in schema.sql but never materialized (tracker
        # installed, not yet synced) -- that's "0 rows", not an error.
        return 0


def _read_table(cfg: Config, table: str, *, limit: int, offset: int) -> dict[str, Any]:
    """Read one page of a whitelisted table. Never mutates; always read-only."""
    empty = {"columns": [], "rows": [], "total": 0, "limit": limit, "offset": offset}
    con = _open_ro(cfg)
    if con is None:
        return empty
    quoted = _quote_ident(table)
    try:
        with contextlib.closing(con):
            cur = con.cursor()
            try:
                cur.execute(f"SELECT COUNT(*) FROM {quoted}")
            except sqlite3.OperationalError:
                return empty
            (total,) = cur.fetchone()
            try:
                cur.execute(
                    f"SELECT * FROM {quoted} ORDER BY rowid DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            except sqlite3.OperationalError:
                # WITHOUT ROWID tables have no `rowid` -- fall back to
                # whatever order SQLite gives us, unordered.
                cur.execute(f"SELECT * FROM {quoted} LIMIT ? OFFSET ?", (limit, offset))
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [[_json_safe(v) for v in row] for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return empty
    return {"columns": columns, "rows": rows, "total": int(total), "limit": limit, "offset": offset}


def _clamp_limit(limit: int) -> int:
    return max(_MIN_LIMIT, min(_MAX_LIMIT, limit))


def _clamp_offset(offset: int) -> int:
    return max(0, offset)


def _tables_info(cfg: Config, schema_tables: set[str]) -> list[dict[str, Any]]:
    return [
        {"name": name, "row_count": _row_count(cfg, name)}
        for name in sorted(schema_tables)
    ]


def _pick_default_table(tables_info: list[dict[str, Any]]) -> str | None:
    """First table alphabetically among those with the most rows.

    Preferring the tracker's largest populated table (rather than always the
    alphabetically-first one) means the page has something to show on first
    load for any tracker with *any* data, even if its alphabetically-first
    table happens to be empty.
    """
    if not tables_info:
        return None
    return min(tables_info, key=lambda t: (-t["row_count"], t["name"]))["name"]


def register_data_routes(
    app: FastAPI,
    router: APIRouter,
    cfg: Config,
    *,
    templates: Jinja2Templates,
    nav_context: Callable[..., dict[str, Any]],
    tracker_title: Callable[[Config, str], str],
    registry: Callable[[], dict[str, Any]],
) -> None:
    @router.get("/data/{tracker}")
    async def api_data_tables(tracker: str) -> dict[str, Any]:
        validate_name(tracker)
        schema_tables = _installed_schema_tables(cfg, tracker)
        if schema_tables is None:
            raise HTTPException(status_code=404, detail=f"tracker not installed: {tracker}")
        return {"tables": _tables_info(cfg, schema_tables)}

    @router.get("/data/{tracker}/{table}")
    async def api_data_table(tracker: str, table: str, limit: int = _DEFAULT_LIMIT, offset: int = 0) -> dict[str, Any]:
        validate_name(tracker)
        schema_tables = _installed_schema_tables(cfg, tracker)
        if schema_tables is None:
            raise HTTPException(status_code=404, detail=f"tracker not installed: {tracker}")
        if table not in schema_tables:
            # Never let an unvalidated name reach SQL -- including a table
            # that legitimately exists in db.sqlite but belongs to a
            # *different* tracker's schema.
            raise HTTPException(
                status_code=404, detail=f"unknown table for tracker {tracker}: {table}"
            )
        return _read_table(cfg, table, limit=_clamp_limit(limit), offset=_clamp_offset(offset))

    @app.get("/t/{tracker}/data", response_class=HTMLResponse)
    async def tracker_data_page(
        request: Request,
        tracker: str,
        table: str | None = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ):
        validate_name(tracker)
        schema_tables = _installed_schema_tables(cfg, tracker)
        if schema_tables is None:
            raise HTTPException(status_code=404, detail=f"tracker not installed: {tracker}")

        tables_info = _tables_info(cfg, schema_tables)
        requested_table = table if table in schema_tables else None
        default_table = requested_table or _pick_default_table(tables_info)

        limit = _clamp_limit(limit)
        offset = _clamp_offset(offset)

        if default_table is not None:
            table_data = _read_table(cfg, default_table, limit=limit, offset=offset)
        else:
            table_data = {"columns": [], "rows": [], "total": 0, "limit": limit, "offset": offset}

        grid_html = None
        empty_html = None
        if table_data["rows"]:
            start = offset + 1
            end = offset + len(table_data["rows"])
            meta_text = f"{table_data['total']} rows · showing {start}–{end}"
            columns = [
                {"field": f"c{i}", "headerName": name}
                for i, name in enumerate(table_data["columns"])
            ]
            rows = [{f"c{i}": v for i, v in enumerate(row)} for row in table_data["rows"]]
            grid_html = ag_grid(columns, rows, page_size=limit)
        else:
            meta_text = "0 rows"
            if not tables_info:
                empty_html = empty_state(
                    "No tables declared for this tracker.",
                    hint="Its schema.sql doesn't define any tables to browse.",
                )
            else:
                empty_html = empty_state(
                    "No data yet.",
                    hint="This table is empty — sync this tracker to collect data.",
                )

        reg = registry()
        return templates.TemplateResponse(
            request=request,
            name="data_browser.html",
            context={
                "active": tracker,
                "tracker": tracker,
                "tracker_title": tracker_title(cfg, tracker),
                "tables": tables_info,
                "default_table": default_table,
                "limit": limit,
                "offset": offset,
                "total": table_data["total"],
                "meta_text": meta_text,
                "grid_html": grid_html,
                "empty_html": empty_html,
                **nav_context(reg, active=tracker),
            },
        )
