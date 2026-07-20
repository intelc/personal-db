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
from personal_db.core.manifest import Manifest, ManifestError, load_manifest
from personal_db.core.sync import tracker_schema_tables
from personal_db.services.daemon.routes.common import validate_name
from personal_db.ui.aggrid import grid as ag_grid
from personal_db.ui.components import empty_state

_MAX_STR_LEN = 4000
_MAX_ROW_VALUE_LEN = 200_000
_DEFAULT_LIMIT = 100
_MIN_LIMIT = 1
_MAX_LIMIT = 500
_SORT_DIRS = ("asc", "desc")


def _quote_ident(name: str) -> str:
    """Double-quote a SQL identifier, escaping embedded quotes.

    `name` is expected to already be whitelist-validated against a tracker's
    schema tables (see `_installed_schema_tables`) before this is called --
    this is belt-and-suspenders, not the primary defense.
    """
    return '"' + name.replace('"', '""') + '"'


def _json_safe(value: Any, *, max_len: int = _MAX_STR_LEN) -> Any:
    """Coerce one SQLite column value into something `json.dumps` can serialize.

    Primitives pass through untouched (so numeric columns still sort/filter
    numerically in the grid); anything else (bytes, etc.) is stringified.
    Long strings are truncated with an ellipsis marker so one giant BLOB/TEXT
    cell can't blow up the response. `max_len` is raised for the single-row
    detail endpoint, which is meant to show the full (if still bounded) value.
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    elif not isinstance(value, str):
        value = str(value)
    if len(value) > max_len:
        return value[:max_len] + "… [truncated]"
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


def _load_manifest(cfg: Config, tracker: str) -> Manifest | None:
    """Best-effort manifest load for column-semantic/time-column enrichment.

    Manifest and schema.sql are separate sources of truth (see CLAUDE.md); a
    missing or unparseable manifest just means "no enrichment available",
    never a 500 -- PRAGMA table_info stays the ground truth for what columns
    actually exist.
    """
    path = cfg.trackers_dir / tracker / "manifest.yaml"
    if not path.is_file():
        return None
    try:
        return load_manifest(path)
    except ManifestError:
        return None


def _open_ro(cfg: Config) -> sqlite3.Connection | None:
    """Open the db read-only, or None if it doesn't exist yet (never synced)."""
    if not cfg.db_path.exists():
        return None
    con = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    return con


def _pragma_columns(con: sqlite3.Connection, table: str) -> list[tuple[str, str]] | None:
    """`[(name, type), ...]` from PRAGMA table_info, or None if not materialized."""
    try:
        rows = con.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
    except sqlite3.OperationalError:
        return None
    if not rows:
        return None
    return [(r[1], r[2]) for r in rows]


def _columns_info(
    table: str, pragma_cols: list[tuple[str, str]] | None, manifest: Manifest | None
) -> list[dict[str, Any]]:
    manifest_cols = {}
    if manifest is not None:
        table_spec = manifest.schema.tables.get(table)
        if table_spec is not None:
            manifest_cols = table_spec.columns
    if pragma_cols is not None:
        return [
            {
                "name": name,
                "type": type_ or None,
                "semantic": manifest_cols[name].semantic if name in manifest_cols else None,
            }
            for name, type_ in pragma_cols
        ]
    # Table declared in schema.sql but not yet materialized (installed, not
    # synced) -- fall back to whatever the manifest declares, if anything.
    return [
        {"name": name, "type": spec.type, "semantic": spec.semantic}
        for name, spec in manifest_cols.items()
    ]


def _time_range(
    con: sqlite3.Connection, table: str, time_column: str | None
) -> dict[str, Any] | None:
    if not time_column:
        return None
    try:
        row = con.execute(
            f"SELECT MIN({_quote_ident(time_column)}), MAX({_quote_ident(time_column)}) "
            f"FROM {_quote_ident(table)}"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row[0] is None:
        return None
    return {"min": _json_safe(row[0]), "max": _json_safe(row[1])}


def _table_info(
    con: sqlite3.Connection | None, table: str, manifest: Manifest | None
) -> dict[str, Any]:
    pragma_cols = _pragma_columns(con, table) if con is not None else None
    row_count = 0
    time_column: str | None = None
    time_range: dict[str, Any] | None = None
    if con is not None and pragma_cols is not None:
        try:
            (row_count,) = con.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()
            row_count = int(row_count)
        except sqlite3.OperationalError:
            row_count = 0
        manifest_time_col = manifest.time_column if manifest is not None else None
        if manifest_time_col is not None and any(c[0] == manifest_time_col for c in pragma_cols):
            time_column = manifest_time_col
            time_range = _time_range(con, table, manifest_time_col)
    return {
        "name": table,
        "row_count": row_count,
        "columns": _columns_info(table, pragma_cols, manifest),
        "time_column": time_column,
        "time_range": time_range,
    }


def _tables_info(cfg: Config, tracker: str, schema_tables: set[str]) -> list[dict[str, Any]]:
    manifest = _load_manifest(cfg, tracker)
    con = _open_ro(cfg)
    try:
        return [_table_info(con, name, manifest) for name in sorted(schema_tables)]
    finally:
        if con is not None:
            con.close()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _read_table(
    cfg: Config,
    table: str,
    *,
    limit: int,
    offset: int,
    sort: str | None = None,
    direction: str = "desc",
    q: str | None = None,
) -> dict[str, Any]:
    """Read one page of a whitelisted table. Never mutates; always read-only."""
    if direction not in _SORT_DIRS:
        raise HTTPException(status_code=400, detail=f"invalid sort direction: {direction!r}")
    empty = {
        "columns": [],
        "rows": [],
        "rowids": None,
        "total": 0,
        "limit": limit,
        "offset": offset,
    }
    con = _open_ro(cfg)
    if con is None:
        return empty
    quoted = _quote_ident(table)
    try:
        with contextlib.closing(con):
            cur = con.cursor()
            try:
                pragma_cols = [r[1] for r in cur.execute(f"PRAGMA table_info({quoted})").fetchall()]
            except sqlite3.OperationalError:
                pragma_cols = []
            if not pragma_cols:
                return empty

            if sort is not None and sort not in pragma_cols:
                raise HTTPException(status_code=400, detail=f"unknown sort column: {sort!r}")

            where_sql = ""
            params: list[Any] = []
            if q:
                like_param = f"%{_escape_like(q)}%"
                clauses = [
                    f"CAST({_quote_ident(c)} AS TEXT) LIKE ? ESCAPE '\\'" for c in pragma_cols
                ]
                where_sql = " WHERE (" + " OR ".join(clauses) + ")"
                params = [like_param] * len(pragma_cols)

            try:
                cur.execute(f"SELECT COUNT(*) FROM {quoted}{where_sql}", params)
                (total,) = cur.fetchone()
            except sqlite3.OperationalError:
                return empty

            order_sql = (
                f" ORDER BY {_quote_ident(sort)} {'ASC' if direction == 'asc' else 'DESC'}"
                if sort is not None
                else ""
            )
            try:
                cur.execute(
                    f"SELECT rowid AS _pdb_rowid, * FROM {quoted}{where_sql}"
                    f"{order_sql or ' ORDER BY rowid DESC'} LIMIT ? OFFSET ?",
                    (*params, limit, offset),
                )
                raw_rows = cur.fetchall()
                columns = [d[0] for d in cur.description][1:]
                rowids: list[int] | None = [int(r[0]) for r in raw_rows]
                rows = [[_json_safe(v) for v in r[1:]] for r in raw_rows]
            except sqlite3.OperationalError:
                # WITHOUT ROWID tables have no `rowid` -- fall back to a plain
                # select, unordered when no explicit sort was requested (same
                # as before rowid existed as a concept here), and disable the
                # per-row detail affordance (rowids=None) for these tables.
                cur.execute(
                    f"SELECT * FROM {quoted}{where_sql}{order_sql} LIMIT ? OFFSET ?",
                    (*params, limit, offset),
                )
                raw_rows = cur.fetchall()
                columns = [d[0] for d in cur.description] if cur.description else []
                rowids = None
                rows = [[_json_safe(v) for v in r] for r in raw_rows]
    except sqlite3.OperationalError:
        return empty
    return {
        "columns": columns,
        "rows": rows,
        "rowids": rowids,
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


def _read_row(cfg: Config, table: str, rowid: int) -> dict[str, Any] | None:
    """Full, (loosely) untruncated row by rowid. None if not found or WITHOUT ROWID."""
    con = _open_ro(cfg)
    if con is None:
        return None
    quoted = _quote_ident(table)
    try:
        with contextlib.closing(con):
            cur = con.execute(f"SELECT * FROM {quoted} WHERE rowid = ?", (rowid,))
            row = cur.fetchone()
            if row is None:
                return None
            columns = [d[0] for d in cur.description] if cur.description else []
    except sqlite3.OperationalError:
        return None
    return {"columns": columns, "row": [_json_safe(v, max_len=_MAX_ROW_VALUE_LEN) for v in row]}


def _clamp_limit(limit: int) -> int:
    return max(_MIN_LIMIT, min(_MAX_LIMIT, limit))


def _clamp_offset(offset: int) -> int:
    return max(0, offset)


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
        return {"tables": _tables_info(cfg, tracker, schema_tables)}

    @router.get("/data/{tracker}/{table}")
    async def api_data_table(
        tracker: str,
        table: str,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
        sort: str | None = None,
        dir: str = "desc",
        q: str | None = None,
    ) -> dict[str, Any]:
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
        return _read_table(
            cfg,
            table,
            limit=_clamp_limit(limit),
            offset=_clamp_offset(offset),
            sort=sort,
            direction=dir,
            q=q,
        )

    @router.get("/data/{tracker}/{table}/row")
    async def api_data_row(tracker: str, table: str, rowid: int) -> dict[str, Any]:
        validate_name(tracker)
        schema_tables = _installed_schema_tables(cfg, tracker)
        if schema_tables is None:
            raise HTTPException(status_code=404, detail=f"tracker not installed: {tracker}")
        if table not in schema_tables:
            raise HTTPException(
                status_code=404, detail=f"unknown table for tracker {tracker}: {table}"
            )
        result = _read_row(cfg, table, rowid)
        if result is None:
            raise HTTPException(status_code=404, detail="row not found")
        return result

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

        tables_info = _tables_info(cfg, tracker, schema_tables)
        requested_table = table if table in schema_tables else None
        default_table = requested_table or _pick_default_table(tables_info)

        limit = _clamp_limit(limit)
        offset = _clamp_offset(offset)

        default_info = next((t for t in tables_info if t["name"] == default_table), None)
        default_sort = default_info["time_column"] if default_info else None

        if default_table is not None:
            table_data = _read_table(
                cfg, default_table, limit=limit, offset=offset, sort=default_sort, direction="desc"
            )
        else:
            table_data = {
                "columns": [],
                "rows": [],
                "rowids": None,
                "total": 0,
                "limit": limit,
                "offset": offset,
            }

        grid_html = None
        empty_html = None
        if table_data["rows"]:
            start = offset + 1
            end = offset + len(table_data["rows"])
            meta_text = f"{table_data['total']} rows · showing {start}–{end}"
            col_meta = {c["name"]: c for c in (default_info["columns"] if default_info else [])}
            columns = []
            for i, name in enumerate(table_data["columns"]):
                col: dict[str, Any] = {"field": f"c{i}", "headerName": name}
                meta = col_meta.get(name)
                if meta and meta.get("semantic"):
                    col["headerTooltip"] = f"{meta.get('type') or '?'} — {meta['semantic']}"
                elif meta and meta.get("type"):
                    col["headerTooltip"] = meta["type"]
                if name == default_sort:
                    col["sort"] = "desc"
                columns.append(col)
            rowids = table_data["rowids"]
            rows = [
                {
                    f"c{i}": v
                    for i, v in enumerate(row)
                } | {"_rowid": rowids[idx] if rowids else None}
                for idx, row in enumerate(table_data["rows"])
            ]
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

        col_count = len(default_info["columns"]) if default_info else 0
        time_range = default_info["time_range"] if default_info else None
        stats_text = f"{col_count} column{'s' if col_count != 1 else ''}"
        if time_range:
            stats_text = f"{time_range['min']} → {time_range['max']} · {stats_text}"

        reg = registry()
        return templates.TemplateResponse(
            request=request,
            name="data_browser.html",
            context={
                "active": tracker,
                "tracker": tracker,
                "tracker_title": tracker_title(cfg, tracker),
                "tables": tables_info,
                "tables_json": tables_info,
                "default_table": default_table,
                "default_sort": default_sort,
                "limit": limit,
                "offset": offset,
                "total": table_data["total"],
                "meta_text": meta_text,
                "stats_text": stats_text,
                "grid_html": grid_html,
                "empty_html": empty_html,
                **nav_context(reg, active=tracker),
            },
        )
