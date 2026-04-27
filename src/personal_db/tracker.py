from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personal_db.config import Config
from personal_db.db import connect


class Cursor:
    """Per-tracker cursor stored in state/cursors.sqlite."""

    def __init__(self, name: str, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self._db = state_dir / "cursors.sqlite"
        self._name = name
        self._init()

    def _init(self) -> None:
        con = sqlite3.connect(self._db)
        con.execute("CREATE TABLE IF NOT EXISTS cursors (name TEXT PRIMARY KEY, value TEXT)")
        con.commit()
        con.close()

    def get(self, default: str | None = None) -> str | None:
        con = sqlite3.connect(self._db)
        row = con.execute("SELECT value FROM cursors WHERE name=?", (self._name,)).fetchone()
        con.close()
        return row[0] if row else default

    def set(self, value: str) -> None:
        con = sqlite3.connect(self._db)
        con.execute(
            "INSERT INTO cursors(name,value) VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (self._name, value),
        )
        con.commit()
        con.close()


@dataclass
class Tracker:
    name: str
    cfg: Config
    manifest: Any  # Manifest | None — None permitted in tests/cursor-only contexts
    log: logging.Logger = field(init=False)
    cursor: Cursor = field(init=False)

    def __post_init__(self):
        self.log = logging.getLogger(f"personal_db.tracker.{self.name}")
        self.cursor = Cursor(self.name, self.cfg.state_dir)

    def upsert(self, table: str, rows: list[dict], key: list[str]) -> int:
        if not rows:
            return 0
        cols = list(rows[0].keys())
        placeholders = ",".join("?" * len(cols))
        update_cols = [c for c in cols if c not in key]
        if update_cols:
            update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
            sql = (
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT({','.join(key)}) DO UPDATE SET {update_clause}"
            )
        else:
            sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        con = connect(self.cfg.db_path)
        con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        con.commit()
        n = con.total_changes
        con.close()
        return n

    def resolve_person(self, alias: str, *, auto_create: bool = True) -> int | None:
        from personal_db.entities import resolve_person

        return resolve_person(self.cfg, alias, auto_create=auto_create)

    def resolve_topic(self, alias: str, *, auto_create: bool = True) -> int | None:
        from personal_db.entities import resolve_topic

        return resolve_topic(self.cfg, alias, auto_create=auto_create)
