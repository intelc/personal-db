from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from personal_db.config import Config

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def connect_db(cfg: Config, *, row_factory: Any | None = None) -> sqlite3.Connection | None:
    try:
        con = sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None
    if row_factory is not None:
        con.row_factory = row_factory
    return con


def meta(text: str) -> str:
    return f'<p class="meta">{text}</p>'


def daily_series(
    cfg: Config,
    table: str,
    column: str,
    days: int,
    *,
    day_column: str = "day",
) -> list[tuple[str, float | int]] | None:
    for identifier in (table, column, day_column):
        if not _IDENT_RE.match(identifier):
            raise ValueError(f"unsafe SQLite identifier: {identifier}")
    con = connect_db(cfg)
    if not con:
        return None
    today = datetime.now().date()
    cutoff = (today - timedelta(days=days - 1)).isoformat()
    try:
        rows = dict(
            con.execute(
                f"SELECT {day_column}, {column} FROM {table} "
                f"WHERE {day_column} >= ? AND {column} IS NOT NULL",
                (cutoff,),
            ).fetchall()
        )
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    items: list[tuple[str, float | int]] = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0)))
    return items
