from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from personal_db.db import connect


class _TrackerLike(Protocol):
    cfg: Any


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def read_env_file(root: Path) -> dict[str, str]:
    env_path = root / ".env"
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def tracker_env(t: _TrackerLike, name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or read_env_file(t.cfg.root).get(name) or default


def read_rows(
    t: _TrackerLike,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    con = connect(t.cfg.db_path)
    try:
        cur = con.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()


def execute(t: _TrackerLike, sql: str, params: tuple[Any, ...] = ()) -> None:
    con = connect(t.cfg.db_path)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def coerce_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
