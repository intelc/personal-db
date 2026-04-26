from __future__ import annotations

import re
from typing import Any

from personal_db.config import Config
from personal_db.db import connect
from personal_db.log_event import log_event
from personal_db.manifest import load_manifest
from personal_db.notes import list_notes, read_note

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
