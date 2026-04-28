"""Within-tracker transformation layer.

A tracker's `ingest.py` can declare zero or more transforms — derived tables
computed from the tracker's own raw tables (or other transforms in the same
tracker). The framework discovers them by walking module attributes for the
`_transform_spec` attribute attached by `@transform`, topo-sorts by their
declared (writes, depends_on) edges, and runs them in order after `sync()`.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from personal_db.db import connect
from personal_db.tracker import Cursor, Tracker


@dataclass
class TransformSpec:
    name: str
    fn: Callable[..., Any]
    writes: str
    depends_on: list[str]


def transform(*, writes: str, depends_on: list[str]):
    """Mark a function in ingest.py as a transform.

    Args:
        writes: table this transform populates (must exist in schema.sql).
        depends_on: tables this transform reads (used for topo-sort).
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._transform_spec = TransformSpec(
            name=fn.__name__,
            fn=fn,
            writes=writes,
            depends_on=list(depends_on),  # defensive copy
        )
        return fn

    return deco


class TransformError(Exception):
    """Raised when transform discovery, validation, or sorting fails."""


def topo_sort(specs: list[TransformSpec]) -> list[TransformSpec]:
    """Return specs in dependency order using Kahn's algorithm.

    Edges: a transform that `depends_on` table T comes after the transform
    that `writes` T. Deps on tables not produced by any spec (i.e. raw
    tables from `ingest.py` or `schema.sql`) are treated as already-satisfied.
    """
    if not specs:
        return []

    # Map writes-target → spec for quick lookup.
    by_writes = {s.writes: s for s in specs}

    # Build edges: spec → set of specs it depends on (within the input set).
    deps: dict[str, set[str]] = {s.name: set() for s in specs}
    for s in specs:
        for d in s.depends_on:
            if d in by_writes:
                deps[s.name].add(by_writes[d].name)

    # Kahn: start with nodes that have no in-set deps; produce stable order
    # (sorted by name within each "ready" wave) so output is deterministic.
    by_name = {s.name: s for s in specs}
    ordered: list[TransformSpec] = []
    remaining = dict(deps)
    while remaining:
        ready = sorted(name for name, d in remaining.items() if not d)
        if not ready:
            cycle_names = sorted(remaining.keys())
            raise TransformError(f"cycle detected among transforms: {cycle_names}")
        for name in ready:
            ordered.append(by_name[name])
            del remaining[name]
            for other_deps in remaining.values():
                other_deps.discard(name)
    return ordered


def validate(specs: list[TransformSpec], *, schema_tables: set[str]) -> None:
    """Run the 4 hard-error rules. Raises TransformError on first violation.

    Rules:
      1. Every `writes` target must exist in schema_tables.
      2. Every `depends_on` entry must be in schema_tables OR be the writes
         target of some other transform in the same set.
      3. No two transforms may share the same `writes` target.
      4. The DAG must be acyclic.
    """
    # Rule 3: duplicate writes
    seen: dict[str, str] = {}
    for s in specs:
        if s.writes in seen:
            raise TransformError(
                f"duplicate writes target '{s.writes}': "
                f"both '{seen[s.writes]}' and '{s.name}' write to it"
            )
        seen[s.writes] = s.name

    # Rule 1: writes target exists
    for s in specs:
        if s.writes not in schema_tables:
            raise TransformError(
                f"transform '{s.name}' writes to '{s.writes}' "
                f"which is not declared in schema.sql"
            )

    # Rule 2: deps satisfied
    transform_outputs = {s.writes for s in specs}
    available = schema_tables | transform_outputs
    for s in specs:
        for d in s.depends_on:
            if d not in available:
                raise TransformError(
                    f"transform '{s.name}' depends_on '{d}' "
                    f"which is neither in schema.sql nor written by another transform"
                )

    # Rule 4: acyclic (delegate to topo_sort, which raises on cycles)
    topo_sort(specs)


def _detect_pk(con: sqlite3.Connection, table: str) -> str:
    """Return the single-column primary key of `table`.

    Uses `PRAGMA table_info(table)`. Each row has columns
    (cid, name, type, notnull, dflt_value, pk). pk > 0 means the column
    participates in the primary key (the value indicates position in a
    composite PK; 0 means not part of any PK).
    """
    rows = list(con.execute(f"PRAGMA table_info({table})"))
    if not rows:
        raise TransformError(f"unknown table: {table}")
    pk_cols = [r[1] for r in rows if r[5] > 0]  # r[1] is name, r[5] is pk position
    if len(pk_cols) == 0:
        raise TransformError(
            f"table '{table}' has no primary key; "
            f"specify source_key= explicitly in enrich()"
        )
    if len(pk_cols) > 1:
        raise TransformError(
            f"table '{table}' has a composite primary key {pk_cols}; "
            f"specify source_key= explicitly in enrich()"
        )
    return pk_cols[0]


@dataclass
class TransformContext:
    """Per-transform invocation context.

    `con` is a fresh sqlite3.Connection with row_factory=Row, used by SQL
    transforms. `cursor` is a per-transform Cursor namespaced as
    "<tracker>:<transform>" so each transform tracks its own progress
    independently of the tracker's own cursor and other transforms.
    `enrich` is a bound method (added in Task 6+) that handles incremental
    enrichment with optional content-addressed caching.
    """

    con: sqlite3.Connection
    cursor: Cursor
    log: logging.Logger
    _tracker: Tracker = field(repr=False)
    _spec: TransformSpec = field(repr=False)

    def enrich(
        self,
        *,
        source: str,
        target: str,
        fn: Callable[[sqlite3.Row], dict],
        source_key: str | None = None,
        dedup_key: Callable[[sqlite3.Row], str] | None = None,  # used in Task 7
        batch_size: int = 1,  # used in Task 8
        where: str | None = None,
    ) -> int:
        """Enrich `source` rows into `target`.

        Opens a separate sqlite connection so per-row commits are independent
        of any outer transaction on self.con.  Uses a cursor stored under
        self.cursor to resume from where the last invocation left off.

        Returns the number of source rows processed in this invocation.
        """
        # Open a separate connection so per-batch commits are independent of
        # any outer transaction the framework may have opened on self.con.
        con = connect(self._tracker.cfg.db_path)
        try:
            con.row_factory = sqlite3.Row
            sk = source_key or _detect_pk(con, source)
            tk = _detect_pk(con, target)

            last = self.cursor.get()

            # Detect source key column type to choose the right sentinel value.
            type_row = next(
                r for r in con.execute(f"PRAGMA table_info({source})") if r[1] == sk
            )
            col_type = type_row[2].upper()
            if "INT" in col_type:
                cursor_val: Any = int(last) if last else 0
            else:
                cursor_val = last if last is not None else ""

            sql = f"SELECT * FROM {source} WHERE {sk} > ?"
            params: list[Any] = [cursor_val]
            if where:
                sql += f" AND ({where})"
            sql += f" ORDER BY {sk}"

            processed = 0
            for row in con.execute(sql, params):
                result = fn(row)
                cols = [tk, *result.keys()]
                placeholders = ",".join("?" * len(cols))
                update_clause = ", ".join(f"{c}=excluded.{c}" for c in result)
                target_sql = (
                    f"INSERT INTO {target} ({','.join(cols)}) VALUES ({placeholders}) "
                    f"ON CONFLICT({tk}) DO UPDATE SET {update_clause}"
                )
                con.execute(target_sql, [row[sk], *result.values()])
                # Per-row commit for now; Task 8 will add real batch_size handling.
                con.commit()
                self.cursor.set(str(row[sk]))
                processed += 1
            return processed
        finally:
            con.close()


def make_context(t: Tracker, spec: TransformSpec) -> TransformContext:
    """Build a TransformContext for a single transform invocation."""
    con = connect(t.cfg.db_path)
    con.row_factory = sqlite3.Row
    cursor = Cursor(name=f"{t.name}:{spec.name}", state_dir=t.cfg.state_dir)
    log = logging.getLogger(f"personal_db.tracker.{t.name}.transform.{spec.name}")
    return TransformContext(con=con, cursor=cursor, log=log, _tracker=t, _spec=spec)
