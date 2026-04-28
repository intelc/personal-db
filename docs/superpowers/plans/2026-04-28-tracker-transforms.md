# Tracker Transforms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a within-tracker transformation layer so a tracker can declare a small DAG of derived tables on top of its raw ingest, including LLM/API enrichments with content-addressed caching.

**Architecture:** New `personal_db.transforms` module exposes a `@transform(writes, depends_on)` decorator that attaches a spec to functions in `ingest.py`. After `mod.sync(t)` runs, the framework discovers decorated functions, validates the DAG against `schema.sql`, topo-sorts, and runs each transform in order. SQL transforms run in a wrapping transaction; the `enrich` helper opens its own connection for per-batch atomic commits independent of the outer transaction. Per-transform cursors are namespaced as `<tracker>:<transform>` in the existing `cursors.sqlite`.

**Tech Stack:** Python 3.11+, SQLite (stdlib `sqlite3`), pytest, the existing `personal_db.tracker.Cursor`/`Tracker`/`Config` primitives.

**Spec:** `docs/superpowers/specs/2026-04-28-tracker-transforms-design.md`

**Note on commits:** This repo is not currently a git repo (`git status` will fail). If you want commit checkpoints, run `git init && git add -A && git commit -m "baseline"` before starting Task 1. Otherwise, treat each "commit" step as "stop and verify everything still works."

**Run all tests with:** `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`

---

## File Structure

**Create:**
- `src/personal_db/transforms.py` — `TransformSpec`, `@transform` decorator, `topo_sort`, `validate`, `TransformContext`, `enrich` helper, `_detect_pk` helper, error types.
- `tests/unit/test_transforms.py` — all unit tests for the transforms module.

**Modify:**
- `src/personal_db/sync.py` — add `_run_transforms(cfg, name, mod)` helper; call from `sync_one` and `backfill_one` after the user's `sync`/`backfill` returns.

**Unchanged (verify after Task 11):**
- `src/personal_db/manifest.py` — no new fields.
- `src/personal_db/tracker.py` — `Cursor` is reused as-is via composition (TransformCursor lives in `transforms.py`).
- All existing trackers — no migration required; the new feature is additive.

---

## Task 1: TransformSpec dataclass + `@transform` decorator

**Files:**
- Create: `src/personal_db/transforms.py`
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_transforms.py` with:

```python
from personal_db.transforms import TransformSpec, transform


def test_decorator_attaches_spec():
    @transform(writes="enriched", depends_on=["raw"])
    def my_transform(t, ctx):
        pass

    spec = my_transform._transform_spec
    assert isinstance(spec, TransformSpec)
    assert spec.name == "my_transform"
    assert spec.writes == "enriched"
    assert spec.depends_on == ["raw"]
    assert spec.fn is my_transform


def test_decorator_returns_function_unchanged():
    """Decorated function should still be callable normally."""

    @transform(writes="t", depends_on=[])
    def f(x, y):
        return x + y

    assert f(2, 3) == 5


def test_decorator_copies_depends_on_list():
    """Mutating the original list shouldn't affect the spec."""
    deps = ["a", "b"]

    @transform(writes="t", depends_on=deps)
    def f(t, ctx):
        pass

    deps.append("c")
    assert f._transform_spec.depends_on == ["a", "b"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: ImportError or ModuleNotFoundError on `personal_db.transforms`.

- [ ] **Step 3: Implement the minimal code to make tests pass**

Create `src/personal_db/transforms.py`:

```python
"""Within-tracker transformation layer.

A tracker's `ingest.py` can declare zero or more transforms — derived tables
computed from the tracker's own raw tables (or other transforms in the same
tracker). The framework discovers them by walking module attributes for the
`_transform_spec` attribute attached by `@transform`, topo-sorts by their
declared (writes, depends_on) edges, and runs them in order after `sync()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/transforms.py tests/unit/test_transforms.py
git commit -m "feat(transforms): add TransformSpec and @transform decorator"
```

---

## Task 2: `topo_sort` with cycle detection

**Files:**
- Modify: `src/personal_db/transforms.py` (add `topo_sort` function and `TransformError` exception)
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_transforms.py`:

```python
import pytest

from personal_db.transforms import TransformError, topo_sort


def _spec(name: str, writes: str, depends_on: list[str]) -> TransformSpec:
    """Build a spec without needing a real function."""
    return TransformSpec(name=name, fn=lambda t, ctx: None, writes=writes, depends_on=depends_on)


def test_topo_sort_linear_chain():
    """A → B → C should order [A, B, C]."""
    a = _spec("a", writes="ta", depends_on=["raw"])
    b = _spec("b", writes="tb", depends_on=["ta"])
    c = _spec("c", writes="tc", depends_on=["tb"])
    ordered = topo_sort([c, a, b])  # input order shouldn't matter
    assert [s.name for s in ordered] == ["a", "b", "c"]


def test_topo_sort_independent_branches():
    """Two independent transforms reading from raw can run in any order, but both must appear."""
    a = _spec("a", writes="ta", depends_on=["raw"])
    b = _spec("b", writes="tb", depends_on=["raw"])
    ordered = topo_sort([a, b])
    assert {s.name for s in ordered} == {"a", "b"}
    assert len(ordered) == 2


def test_topo_sort_diamond():
    """A → B, A → C, B+C → D."""
    a = _spec("a", writes="ta", depends_on=["raw"])
    b = _spec("b", writes="tb", depends_on=["ta"])
    c = _spec("c", writes="tc", depends_on=["ta"])
    d = _spec("d", writes="td", depends_on=["tb", "tc"])
    ordered = [s.name for s in topo_sort([d, c, b, a])]
    # a must come before b and c; b and c must come before d
    assert ordered.index("a") < ordered.index("b")
    assert ordered.index("a") < ordered.index("c")
    assert ordered.index("b") < ordered.index("d")
    assert ordered.index("c") < ordered.index("d")


def test_topo_sort_cycle_raises_with_path():
    """A → B → A is a cycle; the error message should name the involved transforms."""
    a = _spec("a", writes="ta", depends_on=["tb"])
    b = _spec("b", writes="tb", depends_on=["ta"])
    with pytest.raises(TransformError, match="cycle"):
        topo_sort([a, b])


def test_topo_sort_external_deps_ignored():
    """Deps on tables not produced by any transform (i.e. raw tables) don't block ordering."""
    a = _spec("a", writes="ta", depends_on=["raw_only_table"])
    ordered = topo_sort([a])
    assert [s.name for s in ordered] == ["a"]


def test_topo_sort_empty_input():
    assert topo_sort([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: ImportError on `TransformError` and `topo_sort`.

- [ ] **Step 3: Implement `topo_sort` and `TransformError`**

Append to `src/personal_db/transforms.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 9 passed (3 from Task 1 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/transforms.py tests/unit/test_transforms.py
git commit -m "feat(transforms): add topo_sort with cycle detection"
```

---

## Task 3: `validate` function (4 hard-error rules)

**Files:**
- Modify: `src/personal_db/transforms.py` (add `validate`)
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_transforms.py`:

```python
from personal_db.transforms import validate


def test_validate_rejects_writes_not_in_schema():
    spec = _spec("t", writes="nonexistent", depends_on=["raw"])
    with pytest.raises(TransformError, match="writes"):
        validate([spec], schema_tables={"raw"})


def test_validate_rejects_dep_not_in_schema_and_not_a_transform_target():
    spec = _spec("t", writes="enriched", depends_on=["does_not_exist"])
    with pytest.raises(TransformError, match="depends_on"):
        validate([spec], schema_tables={"enriched"})


def test_validate_accepts_dep_satisfied_by_other_transform():
    a = _spec("a", writes="mid", depends_on=["raw"])
    b = _spec("b", writes="final", depends_on=["mid"])
    # Neither depends on a table that isn't covered.
    validate([a, b], schema_tables={"raw", "mid", "final"})


def test_validate_rejects_duplicate_writes():
    a = _spec("a", writes="t", depends_on=["raw"])
    b = _spec("b", writes="t", depends_on=["raw"])
    with pytest.raises(TransformError, match="duplicate"):
        validate([a, b], schema_tables={"raw", "t"})


def test_validate_rejects_cycle():
    a = _spec("a", writes="ta", depends_on=["tb"])
    b = _spec("b", writes="tb", depends_on=["ta"])
    with pytest.raises(TransformError, match="cycle"):
        validate([a, b], schema_tables={"ta", "tb"})


def test_validate_passes_for_well_formed_dag():
    a = _spec("a", writes="ta", depends_on=["raw"])
    b = _spec("b", writes="tb", depends_on=["ta"])
    validate([a, b], schema_tables={"raw", "ta", "tb"})  # no exception
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: ImportError on `validate`.

- [ ] **Step 3: Implement `validate`**

Append to `src/personal_db/transforms.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/transforms.py tests/unit/test_transforms.py
git commit -m "feat(transforms): add validate with 4 hard-error rules"
```

---

## Task 4: `_detect_pk` helper (for `enrich`'s default `source_key`)

**Files:**
- Modify: `src/personal_db/transforms.py` (add `_detect_pk`)
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_transforms.py`:

```python
import sqlite3

from personal_db.transforms import _detect_pk


def _make_db(*ddls: str) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    for ddl in ddls:
        con.execute(ddl)
    return con


def test_detect_pk_single_column():
    con = _make_db("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    assert _detect_pk(con, "t") == "id"


def test_detect_pk_named_column():
    con = _make_db("CREATE TABLE t (uuid TEXT PRIMARY KEY, val INTEGER)")
    assert _detect_pk(con, "t") == "uuid"


def test_detect_pk_composite_raises():
    con = _make_db("CREATE TABLE t (a TEXT, b TEXT, val INTEGER, PRIMARY KEY (a, b))")
    with pytest.raises(TransformError, match="composite"):
        _detect_pk(con, "t")


def test_detect_pk_no_pk_raises():
    con = _make_db("CREATE TABLE t (a TEXT, b TEXT)")
    with pytest.raises(TransformError, match="no primary key"):
        _detect_pk(con, "t")


def test_detect_pk_unknown_table_raises():
    con = _make_db()
    with pytest.raises(TransformError, match="unknown"):
        _detect_pk(con, "nonexistent")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: ImportError on `_detect_pk`.

- [ ] **Step 3: Implement `_detect_pk`**

Append to `src/personal_db/transforms.py`:

```python
import sqlite3


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
```

(Note: the `import sqlite3` line goes at the top of the file alongside the existing imports — move it there if you prefer, or leave it inline; Python deduplicates imports.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/transforms.py tests/unit/test_transforms.py
git commit -m "feat(transforms): add _detect_pk helper"
```

---

## Task 5: `TransformContext` + namespaced cursor + factory

**Files:**
- Modify: `src/personal_db/transforms.py` (add `TransformContext`, `make_context`)
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_transforms.py`:

```python
from personal_db.config import Config
from personal_db.tracker import Tracker
from personal_db.transforms import TransformContext, make_context


def test_make_context_returns_namespaced_cursor(tmp_root):
    """Two transforms in the same tracker get independent cursor state."""
    cfg = Config(root=tmp_root)
    spec_a = _spec("a", writes="ta", depends_on=["raw"])
    spec_b = _spec("b", writes="tb", depends_on=["raw"])
    t = Tracker(name="mytracker", cfg=cfg, manifest=None)

    ctx_a = make_context(t, spec_a)
    ctx_b = make_context(t, spec_b)

    ctx_a.cursor.set("100")
    ctx_b.cursor.set("200")

    # Each transform's cursor is independent
    assert ctx_a.cursor.get() == "100"
    assert ctx_b.cursor.get() == "200"

    # And the tracker's own cursor is independent of both
    assert t.cursor.get() is None


def test_make_context_provides_sqlite_connection_with_row_factory(tmp_root):
    cfg = Config(root=tmp_root)
    # init_db so cfg.db_path is a valid sqlite file
    from personal_db.db import init_db
    init_db(cfg.db_path)

    t = Tracker(name="mytracker", cfg=cfg, manifest=None)
    spec = _spec("a", writes="ta", depends_on=["raw"])
    ctx = make_context(t, spec)

    assert isinstance(ctx, TransformContext)
    assert ctx.con.row_factory is sqlite3.Row
    # Cursor query returns Row objects (which support both index and key access)
    row = ctx.con.execute("SELECT 1 AS x").fetchone()
    assert row["x"] == 1
    assert row[0] == 1


def test_make_context_attaches_logger(tmp_root):
    cfg = Config(root=tmp_root)
    t = Tracker(name="mytracker", cfg=cfg, manifest=None)
    spec = _spec("geocoded", writes="geocoded_locations", depends_on=["raw_locations"])
    ctx = make_context(t, spec)

    # Logger name should identify both tracker and transform
    assert "mytracker" in ctx.log.name
    assert "geocoded" in ctx.log.name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: ImportError on `TransformContext` and `make_context`.

- [ ] **Step 3: Implement `TransformContext` and `make_context`**

Append to `src/personal_db/transforms.py`:

```python
import logging
from dataclasses import dataclass, field

from personal_db.db import connect
from personal_db.tracker import Cursor, Tracker


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

    # `enrich` will be added as a method in Task 6.


def make_context(t: Tracker, spec: TransformSpec) -> TransformContext:
    """Build a TransformContext for a single transform invocation."""
    con = connect(t.cfg.db_path)
    cursor = Cursor(name=f"{t.name}:{spec.name}", state_dir=t.cfg.state_dir)
    log = logging.getLogger(f"personal_db.tracker.{t.name}.transform.{spec.name}")
    return TransformContext(con=con, cursor=cursor, log=log, _tracker=t, _spec=spec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 23 passed.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/transforms.py tests/unit/test_transforms.py
git commit -m "feat(transforms): add TransformContext and make_context factory"
```

---

## Task 6: `enrich` — basic (no dedup, no batching)

**Files:**
- Modify: `src/personal_db/transforms.py` (add `enrich` method to `TransformContext`)
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_transforms.py`:

```python
from personal_db.db import init_db


def _setup_db_with_source_table(tmp_root, rows: list[dict]) -> None:
    """Create db.sqlite with a `raw` table populated and an empty `enriched` target."""
    init_db(Config(root=tmp_root).db_path)
    con = sqlite3.connect(Config(root=tmp_root).db_path)
    con.execute("CREATE TABLE raw (id INTEGER PRIMARY KEY, val INTEGER)")
    con.execute(
        "CREATE TABLE enriched (source_id INTEGER PRIMARY KEY, doubled INTEGER)"
    )
    con.executemany("INSERT INTO raw (id, val) VALUES (:id, :val)", rows)
    con.commit()
    con.close()


def test_enrich_processes_all_rows_when_cursor_empty(tmp_root):
    _setup_db_with_source_table(
        tmp_root, [{"id": 1, "val": 10}, {"id": 2, "val": 20}, {"id": 3, "val": 30}]
    )
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("doubler", "enriched", ["raw"]))

    n = ctx.enrich(
        source="raw",
        target="enriched",
        fn=lambda row: {"doubled": row["val"] * 2},
    )

    assert n == 3
    con = sqlite3.connect(cfg.db_path)
    out = con.execute("SELECT source_id, doubled FROM enriched ORDER BY source_id").fetchall()
    assert out == [(1, 20), (2, 40), (3, 60)]


def test_enrich_advances_cursor_to_last_processed(tmp_root):
    _setup_db_with_source_table(tmp_root, [{"id": 1, "val": 10}, {"id": 2, "val": 20}])
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("doubler", "enriched", ["raw"]))

    ctx.enrich(source="raw", target="enriched", fn=lambda row: {"doubled": row["val"] * 2})

    # Re-create the context and verify cursor persisted
    ctx2 = make_context(t, _spec("doubler", "enriched", ["raw"]))
    assert ctx2.cursor.get() == "2"


def test_enrich_skips_already_processed_rows(tmp_root):
    _setup_db_with_source_table(tmp_root, [{"id": 1, "val": 10}, {"id": 2, "val": 20}])
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("doubler", "enriched", ["raw"]))

    ctx.enrich(source="raw", target="enriched", fn=lambda row: {"doubled": row["val"] * 2})

    # Insert a third row, run again
    con = sqlite3.connect(cfg.db_path)
    con.execute("INSERT INTO raw (id, val) VALUES (3, 30)")
    con.commit()
    con.close()

    calls = []

    def counting_fn(row):
        calls.append(row["id"])
        return {"doubled": row["val"] * 2}

    ctx2 = make_context(t, _spec("doubler", "enriched", ["raw"]))
    n = ctx2.enrich(source="raw", target="enriched", fn=counting_fn)

    assert n == 1
    assert calls == [3]  # only the new row


def test_enrich_uses_explicit_source_key(tmp_root):
    """source_key= overrides PK auto-detection."""
    init_db(Config(root=tmp_root).db_path)
    con = sqlite3.connect(Config(root=tmp_root).db_path)
    con.execute("CREATE TABLE raw (rowid_alias INTEGER PRIMARY KEY, val INTEGER, ord INTEGER UNIQUE)")
    con.execute("CREATE TABLE enriched (source_ord INTEGER PRIMARY KEY, doubled INTEGER)")
    con.executemany(
        "INSERT INTO raw (rowid_alias, val, ord) VALUES (:r, :v, :o)",
        [{"r": 100, "v": 1, "o": 1}, {"r": 200, "v": 2, "o": 2}],
    )
    con.commit()
    con.close()

    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("d", "enriched", ["raw"]))

    ctx.enrich(
        source="raw",
        target="enriched",
        fn=lambda row: {"doubled": row["val"] * 2},
        source_key="ord",
    )

    con = sqlite3.connect(cfg.db_path)
    out = con.execute("SELECT source_ord, doubled FROM enriched ORDER BY source_ord").fetchall()
    assert out == [(1, 2), (2, 4)]
    con.close()


def test_enrich_respects_where_clause(tmp_root):
    _setup_db_with_source_table(
        tmp_root, [{"id": 1, "val": 10}, {"id": 2, "val": 20}, {"id": 3, "val": 30}]
    )
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("d", "enriched", ["raw"]))

    n = ctx.enrich(
        source="raw",
        target="enriched",
        fn=lambda row: {"doubled": row["val"] * 2},
        where="val > 15",
    )

    assert n == 2
    con = sqlite3.connect(cfg.db_path)
    ids = [r[0] for r in con.execute("SELECT source_id FROM enriched ORDER BY source_id")]
    assert ids == [2, 3]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: AttributeError on `ctx.enrich`.

- [ ] **Step 3: Implement `enrich` (no dedup yet)**

Add this method to the `TransformContext` dataclass in `src/personal_db/transforms.py` (replace the `# enrich will be added` comment):

```python
    def enrich(
        self,
        *,
        source: str,
        target: str,
        fn: Callable[[sqlite3.Row], dict],
        source_key: str | None = None,
        dedup_key: Callable[[sqlite3.Row], str] | None = None,  # used in Task 7
        batch_size: int = 1,                                    # used in Task 8
        where: str | None = None,
    ) -> int:
        """Enrich `source` rows into `target`. See spec for full semantics.

        Returns the number of source rows processed in this invocation.
        """
        # Open a separate connection so per-batch commits are independent of
        # any outer transaction the framework may have opened on self.con.
        con = connect(self._tracker.cfg.db_path)
        try:
            con.row_factory = sqlite3.Row
            sk = source_key or _detect_pk(con, source)

            last = self.cursor.get()
            params: list = []
            sql = f"SELECT * FROM {source} WHERE {sk} > ?"
            params.append(last if last is not None else "")
            if where:
                sql += f" AND ({where})"
            sql += f" ORDER BY {sk}"

            processed = 0
            for row in con.execute(sql, params):
                result = fn(row)
                cols = [sk, *result.keys()]
                placeholders = ",".join("?" * len(cols))
                update_clause = ", ".join(f"{c}=excluded.{c}" for c in result.keys())
                target_sql = (
                    f"INSERT INTO {target} ({','.join(cols)}) VALUES ({placeholders}) "
                    f"ON CONFLICT({sk}) DO UPDATE SET {update_clause}"
                )
                con.execute(target_sql, [row[sk], *result.values()])
                # Per-row commit for now; Task 8 will add real batch_size handling.
                con.commit()
                self.cursor.set(str(row[sk]))
                processed += 1
            return processed
        finally:
            con.close()
```

Note: the `last if last is not None else ""` comparison is a temporary workaround — for INTEGER source_keys we want `0` as the sentinel and for TEXT we want `""`. SQLite's type affinity makes `> ""` work for both because empty string < any non-empty value, and `> ""` for INTEGER source_key gets coerced. If a test fails due to this, change the sentinel logic to inspect the source column's type via `PRAGMA table_info`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 28 passed.

If `test_enrich_skips_already_processed_rows` fails because the cursor comparison treats `"2" > "10"` lexically (TEXT comparison), change the sentinel logic in `enrich` to:

```python
# Detect column type to choose the right sentinel.
type_row = next(r for r in con.execute(f"PRAGMA table_info({source})") if r[1] == sk)
col_type = type_row[2].upper()
if "INT" in col_type:
    cursor_val = int(last) if last else 0
else:
    cursor_val = last if last is not None else ""
params = [cursor_val]
```

Then re-run.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/transforms.py tests/unit/test_transforms.py
git commit -m "feat(transforms): add enrich (basic, no dedup or batching)"
```

---

## Task 7: `enrich` — content-addressed dedup cache

**Files:**
- Modify: `src/personal_db/transforms.py` (extend `enrich` to use `dedup_key`)
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_transforms.py`:

```python
def test_enrich_dedup_calls_fn_once_per_unique_key(tmp_root):
    """Two source rows with the same dedup_key should result in one fn call."""
    _setup_db_with_source_table(
        tmp_root,
        [
            {"id": 1, "val": 10},
            {"id": 2, "val": 10},  # same val → same dedup key
            {"id": 3, "val": 20},
        ],
    )
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("d", "enriched", ["raw"]))

    calls = []

    def fn(row):
        calls.append(row["id"])
        return {"doubled": row["val"] * 2}

    n = ctx.enrich(
        source="raw",
        target="enriched",
        fn=fn,
        dedup_key=lambda r: str(r["val"]),
    )

    assert n == 3
    assert sorted(calls) == [1, 3]  # row 2 reused row 1's cached result

    # All three target rows present
    con = sqlite3.connect(cfg.db_path)
    out = con.execute("SELECT source_id, doubled FROM enriched ORDER BY source_id").fetchall()
    assert out == [(1, 20), (2, 20), (3, 40)]


def test_enrich_dedup_cache_persists_across_invocations(tmp_root):
    _setup_db_with_source_table(tmp_root, [{"id": 1, "val": 10}])
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)

    calls = []

    def fn(row):
        calls.append(row["id"])
        return {"doubled": row["val"] * 2}

    ctx = make_context(t, _spec("d", "enriched", ["raw"]))
    ctx.enrich(source="raw", target="enriched", fn=fn, dedup_key=lambda r: str(r["val"]))
    assert calls == [1]

    # Insert another row with the same val and re-run
    con = sqlite3.connect(cfg.db_path)
    con.execute("INSERT INTO raw (id, val) VALUES (2, 10)")
    con.commit()
    con.close()

    ctx2 = make_context(t, _spec("d", "enriched", ["raw"]))
    ctx2.enrich(source="raw", target="enriched", fn=fn, dedup_key=lambda r: str(r["val"]))

    # fn was NOT called again — the cache survived
    assert calls == [1]


def test_enrich_dedup_creates_underscore_prefixed_cache_table(tmp_root):
    _setup_db_with_source_table(tmp_root, [{"id": 1, "val": 10}])
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("d", "enriched", ["raw"]))

    ctx.enrich(
        source="raw", target="enriched",
        fn=lambda r: {"doubled": r["val"] * 2},
        dedup_key=lambda r: str(r["val"]),
    )

    con = sqlite3.connect(cfg.db_path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "_enriched_cache" in tables
    rows = con.execute("SELECT key, value FROM _enriched_cache").fetchall()
    assert rows == [("10", '{"doubled": 20}')]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 3 of the new tests fail (existing 28 still pass).

- [ ] **Step 3: Implement dedup cache logic**

Add a `json` import at the top of `src/personal_db/transforms.py` if not already present. Modify the `enrich` method body to consult/populate the cache when `dedup_key` is provided. Replace the row-processing loop:

```python
            # Lazily ensure cache table exists when dedup is in play.
            cache_table = f"_{target}_cache"
            if dedup_key is not None:
                con.execute(
                    f"CREATE TABLE IF NOT EXISTS {cache_table} "
                    f"(key TEXT PRIMARY KEY, value TEXT)"
                )
                con.commit()

            processed = 0
            for row in con.execute(sql, params):
                if dedup_key is not None:
                    k = dedup_key(row)
                    cached = con.execute(
                        f"SELECT value FROM {cache_table} WHERE key=?", (k,)
                    ).fetchone()
                    if cached is not None:
                        result = json.loads(cached[0])
                    else:
                        result = fn(row)
                        con.execute(
                            f"INSERT INTO {cache_table} (key, value) VALUES (?, ?)",
                            (k, json.dumps(result)),
                        )
                else:
                    result = fn(row)

                cols = [sk, *result.keys()]
                placeholders = ",".join("?" * len(cols))
                update_clause = ", ".join(f"{c}=excluded.{c}" for c in result.keys())
                target_sql = (
                    f"INSERT INTO {target} ({','.join(cols)}) VALUES ({placeholders}) "
                    f"ON CONFLICT({sk}) DO UPDATE SET {update_clause}"
                )
                con.execute(target_sql, [row[sk], *result.values()])
                con.commit()
                self.cursor.set(str(row[sk]))
                processed += 1
            return processed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 31 passed.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/transforms.py tests/unit/test_transforms.py
git commit -m "feat(transforms): add dedup cache to enrich"
```

---

## Task 8: `enrich` — `batch_size` and per-batch atomic commits with failure recovery

**Files:**
- Modify: `src/personal_db/transforms.py` (replace per-row commits with per-batch commits)
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_transforms.py`:

```python
def test_enrich_batch_atomic_rollback_on_failure(tmp_root):
    """fn raises on row 5; rows 1-4 (one full batch of 4) should persist; rows 5-8 should not."""
    _setup_db_with_source_table(
        tmp_root, [{"id": i, "val": i * 10} for i in range(1, 9)]
    )
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("d", "enriched", ["raw"]))

    def fn(row):
        if row["id"] == 5:
            raise RuntimeError("simulated API failure")
        return {"doubled": row["val"] * 2}

    with pytest.raises(RuntimeError, match="simulated"):
        ctx.enrich(source="raw", target="enriched", fn=fn, batch_size=4)

    # First batch (ids 1-4) committed; second batch (ids 5-8) rolled back.
    con = sqlite3.connect(cfg.db_path)
    rows = con.execute("SELECT source_id FROM enriched ORDER BY source_id").fetchall()
    assert [r[0] for r in rows] == [1, 2, 3, 4]

    # Cursor advanced to the last successful row (4), not past it.
    ctx2 = make_context(t, _spec("d", "enriched", ["raw"]))
    assert ctx2.cursor.get() == "4"


def test_enrich_resumes_after_failure(tmp_root):
    """A second sync after a failed first sync should pick up where it left off."""
    _setup_db_with_source_table(
        tmp_root, [{"id": i, "val": i * 10} for i in range(1, 5)]
    )
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)

    fail_on = {"id": 3}

    def fn(row):
        if row["id"] == fail_on["id"]:
            raise RuntimeError("flake")
        return {"doubled": row["val"] * 2}

    ctx = make_context(t, _spec("d", "enriched", ["raw"]))
    with pytest.raises(RuntimeError):
        ctx.enrich(source="raw", target="enriched", fn=fn, batch_size=2)

    # Batch of [1,2] committed; [3,4] rolled back.
    con = sqlite3.connect(cfg.db_path)
    assert [r[0] for r in con.execute("SELECT source_id FROM enriched ORDER BY source_id")] == [1, 2]
    con.close()

    # Now "fix" the API and re-run
    fail_on["id"] = -1  # never fires
    ctx2 = make_context(t, _spec("d", "enriched", ["raw"]))
    n = ctx2.enrich(source="raw", target="enriched", fn=fn, batch_size=2)
    assert n == 2  # rows 3 and 4

    con = sqlite3.connect(cfg.db_path)
    assert [r[0] for r in con.execute("SELECT source_id FROM enriched ORDER BY source_id")] == [1, 2, 3, 4]


def test_enrich_dedup_cache_writes_rolled_back_with_batch(tmp_root):
    """A cache write inside a failed batch should NOT persist."""
    _setup_db_with_source_table(
        tmp_root,
        [{"id": 1, "val": 10}, {"id": 2, "val": 20}, {"id": 3, "val": 30}],
    )
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("d", "enriched", ["raw"]))

    def fn(row):
        if row["id"] == 2:
            raise RuntimeError("flake")
        return {"doubled": row["val"] * 2}

    with pytest.raises(RuntimeError):
        ctx.enrich(
            source="raw", target="enriched", fn=fn,
            dedup_key=lambda r: str(r["val"]), batch_size=3,
        )

    # The whole batch rolled back: no rows in target, no entries in cache.
    con = sqlite3.connect(cfg.db_path)
    assert con.execute("SELECT count(*) FROM enriched").fetchone()[0] == 0
    # Cache table may or may not exist (depending on creation timing); if it
    # exists, it should be empty.
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "_enriched_cache" in tables:
        assert con.execute("SELECT count(*) FROM _enriched_cache").fetchone()[0] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: the 3 new tests fail (existing 31 still pass).

- [ ] **Step 3: Refactor `enrich` to use per-batch transactions**

Replace the row-processing loop in `enrich` with explicit BEGIN/COMMIT batching. The cache-table creation must happen inside the first batch's transaction so it rolls back too if the batch fails — actually, since `CREATE TABLE IF NOT EXISTS` is cheap and idempotent, doing it once outside the batch loop is fine; the test allows the table to exist as long as it's empty.

Replace the loop body in `enrich`:

```python
            # Pre-fetch all rows so we can iterate in batch chunks.
            rows = list(con.execute(sql, params))

            processed = 0
            for batch_start in range(0, len(rows), batch_size):
                batch = rows[batch_start : batch_start + batch_size]
                con.execute("BEGIN")
                try:
                    for row in batch:
                        if dedup_key is not None:
                            k = dedup_key(row)
                            cached = con.execute(
                                f"SELECT value FROM {cache_table} WHERE key=?", (k,)
                            ).fetchone()
                            if cached is not None:
                                result = json.loads(cached[0])
                            else:
                                result = fn(row)
                                con.execute(
                                    f"INSERT INTO {cache_table} (key, value) VALUES (?, ?)",
                                    (k, json.dumps(result)),
                                )
                        else:
                            result = fn(row)

                        cols = [sk, *result.keys()]
                        placeholders = ",".join("?" * len(cols))
                        update_clause = ", ".join(f"{c}=excluded.{c}" for c in result.keys())
                        target_sql = (
                            f"INSERT INTO {target} ({','.join(cols)}) VALUES ({placeholders}) "
                            f"ON CONFLICT({sk}) DO UPDATE SET {update_clause}"
                        )
                        con.execute(target_sql, [row[sk], *result.values()])
                        # Cursor write happens inside this transaction by going
                        # through a separate sqlite handle, since Cursor opens
                        # its own connection. We defer the cursor write until
                        # *after* the COMMIT below, so it advances only if the
                        # batch persists.
                        processed += 1
                    con.commit()
                    # All rows in this batch persisted — advance cursor to last row's key.
                    self.cursor.set(str(batch[-1][sk]))
                except Exception:
                    con.rollback()
                    raise
            return processed
```

Note: `self.cursor.set` writes to a separate sqlite file (`state/cursors.sqlite`) on its own connection, so it's NOT part of the `BEGIN/COMMIT` transaction on `con`. We deliberately call it AFTER `con.commit()` so it advances only if the batch persisted. If `con.commit()` succeeds but the cursor write fails, we'd reprocess the last batch on next sync — harmless.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 34 passed.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/transforms.py tests/unit/test_transforms.py
git commit -m "feat(transforms): add per-batch atomic commits to enrich"
```

---

## Task 9: `sync.py` — `_run_transforms` helper + integrate into `sync_one`

**Files:**
- Modify: `src/personal_db/sync.py`
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_transforms.py`:

```python
import textwrap

from personal_db.sync import sync_one


def _write_tracker(tmp_root: Path, name: str, schema: str, ingest: str, manifest: str) -> Path:
    """Create a fake tracker dir under tmp_root/trackers/<name>/."""
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "schema.sql").write_text(schema)
    (d / "ingest.py").write_text(ingest)
    (d / "manifest.yaml").write_text(manifest)
    return d


def test_sync_one_runs_transforms_after_ingest(tmp_root):
    schema = textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS raw (id INTEGER PRIMARY KEY, val INTEGER);
        CREATE TABLE IF NOT EXISTS doubled (source_id INTEGER PRIMARY KEY, d INTEGER);
        CREATE TABLE IF NOT EXISTS sum_t (k TEXT PRIMARY KEY, total INTEGER);
    """).strip()

    ingest = textwrap.dedent("""
        from personal_db.tracker import Tracker
        from personal_db.transforms import transform

        def sync(t: Tracker) -> None:
            t.upsert("raw", [{"id": 1, "val": 10}, {"id": 2, "val": 20}], key=["id"])

        @transform(writes="doubled", depends_on=["raw"])
        def double_them(t, ctx):
            ctx.enrich(source="raw", target="doubled", fn=lambda r: {"d": r["val"] * 2})

        @transform(writes="sum_t", depends_on=["doubled"])
        def total(t, ctx):
            ctx.con.execute("DELETE FROM sum_t")
            ctx.con.execute("INSERT INTO sum_t (k, total) SELECT 'all', sum(d) FROM doubled")
            ctx.con.commit()
    """).strip()

    manifest = textwrap.dedent("""
        name: x
        description: test
        permission_type: none
        time_column: ts
        granularity: event
        schema:
          tables:
            raw:
              columns:
                id: {type: INTEGER, semantic: pk}
                val: {type: INTEGER, semantic: value}
    """).strip()

    _write_tracker(tmp_root, "x", schema, ingest, manifest)

    cfg = Config(root=tmp_root)
    sync_one(cfg, "x")

    con = sqlite3.connect(cfg.db_path)
    raw = con.execute("SELECT id, val FROM raw ORDER BY id").fetchall()
    doubled = con.execute("SELECT source_id, d FROM doubled ORDER BY source_id").fetchall()
    summed = con.execute("SELECT k, total FROM sum_t").fetchall()

    assert raw == [(1, 10), (2, 20)]
    assert doubled == [(1, 20), (2, 40)]
    assert summed == [("all", 60)]


def test_sync_one_skips_transforms_when_none_declared(tmp_root):
    """A tracker with no @transform-decorated functions should still sync cleanly."""
    schema = "CREATE TABLE IF NOT EXISTS raw (id INTEGER PRIMARY KEY, val INTEGER);"
    ingest = textwrap.dedent("""
        from personal_db.tracker import Tracker
        def sync(t: Tracker) -> None:
            t.upsert("raw", [{"id": 1, "val": 99}], key=["id"])
    """).strip()
    manifest = textwrap.dedent("""
        name: y
        description: test
        permission_type: none
        time_column: ts
        granularity: event
        schema:
          tables:
            raw:
              columns:
                id: {type: INTEGER, semantic: pk}
    """).strip()

    _write_tracker(tmp_root, "y", schema, ingest, manifest)
    cfg = Config(root=tmp_root)
    sync_one(cfg, "y")  # must not raise

    con = sqlite3.connect(cfg.db_path)
    assert con.execute("SELECT val FROM raw WHERE id=1").fetchone()[0] == 99
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py::test_sync_one_runs_transforms_after_ingest -v`
Expected: FAIL — `doubled` and `sum_t` tables exist (created by schema) but contain no rows because transforms aren't being called.

- [ ] **Step 3: Implement `_run_transforms` and call it from `sync_one`**

Edit `src/personal_db/sync.py`. Add this import near the top:

```python
import re
```

(should already be there — verify)

Add a new helper function between `_load_ingest_module` and `_last_run_path`:

```python
def _extract_schema_tables(schema_sql: str) -> set[str]:
    """Pull table names out of CREATE TABLE statements in schema.sql.

    Used to validate that every @transform's `writes` and `depends_on` refer
    to tables actually declared in the tracker's schema.
    """
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?(\w+)[\"`]?",
        re.IGNORECASE,
    )
    return set(pattern.findall(schema_sql))


def _run_transforms(cfg: Config, name: str, mod, tracker_dir: Path) -> None:
    """Discover @transform functions in `mod`, validate the DAG, and run in topo order.

    Errors per-transform are caught and logged to sync_errors.jsonl; downstream
    transforms whose deps failed are skipped, but independent branches still run.
    """
    from personal_db.manifest import load_manifest
    from personal_db.tracker import Tracker
    from personal_db.transforms import (
        TransformError,
        make_context,
        topo_sort,
        validate,
    )

    specs = [
        v._transform_spec
        for v in vars(mod).values()
        if hasattr(v, "_transform_spec")
    ]
    if not specs:
        return

    schema_sql = (tracker_dir / "schema.sql").read_text()
    schema_tables = _extract_schema_tables(schema_sql)

    try:
        validate(specs, schema_tables=schema_tables)
    except TransformError as e:
        _record_transform_error(cfg, name, "<validation>", e)
        return

    manifest = load_manifest(tracker_dir / "manifest.yaml")
    t = Tracker(name=name, cfg=cfg, manifest=manifest)

    failed_writes: set[str] = set()
    for spec in topo_sort(specs):
        if any(d in failed_writes for d in spec.depends_on):
            # Upstream transform failed this tick; skip downstream.
            continue
        ctx = make_context(t, spec)
        try:
            spec.fn(t, ctx)
        except Exception as e:
            failed_writes.add(spec.writes)
            _record_transform_error(cfg, name, spec.name, e)
        finally:
            ctx.con.close()


def _record_transform_error(cfg: Config, tracker: str, transform_name: str, err: Exception) -> None:
    err_path = cfg.state_dir / "sync_errors.jsonl"
    with err_path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "tracker": tracker,
                    "transform": transform_name,
                    "error": str(err),
                    "tb": traceback.format_exc(),
                }
            )
            + "\n"
        )
```

Modify `sync_one` to call `_run_transforms` after `mod.sync(t)`:

```python
def sync_one(cfg: Config, name: str) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.sync(t)
    _run_transforms(cfg, name, mod, tracker_dir)   # NEW
    _write_last_run(cfg, name, datetime.now(UTC).isoformat())
    _store_horizon(cfg, name, manifest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 36 passed.

Also run the full unit test suite to make sure no existing test broke:

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: all green (whatever the existing baseline is, no new failures).

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/sync.py tests/unit/test_transforms.py
git commit -m "feat(sync): run @transforms after sync_one's ingest"
```

---

## Task 10: `sync.py` — verify error isolation behavior

**Files:**
- Test: `tests/unit/test_transforms.py` (no production code change — `_run_transforms` already implements the behavior in Task 9; this task is a *contract test* to lock it in)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_transforms.py`:

```python
import json as _json


def test_failed_transform_does_not_break_sync_and_is_logged(tmp_root):
    schema = textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS raw (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS bad (source_id INTEGER PRIMARY KEY);
    """).strip()

    ingest = textwrap.dedent("""
        from personal_db.transforms import transform

        def sync(t):
            t.upsert("raw", [{"id": 1}], key=["id"])

        @transform(writes="bad", depends_on=["raw"])
        def explode(t, ctx):
            raise RuntimeError("kaboom")
    """).strip()

    manifest = textwrap.dedent("""
        name: z
        description: t
        permission_type: none
        time_column: ts
        granularity: event
        schema:
          tables:
            raw:
              columns:
                id: {type: INTEGER, semantic: pk}
    """).strip()

    _write_tracker(tmp_root, "z", schema, ingest, manifest)
    cfg = Config(root=tmp_root)

    # sync_one should NOT raise even though the transform did
    sync_one(cfg, "z")

    # Error logged to sync_errors.jsonl
    err_path = cfg.state_dir / "sync_errors.jsonl"
    assert err_path.exists()
    lines = err_path.read_text().strip().splitlines()
    parsed = [_json.loads(line) for line in lines]
    matches = [p for p in parsed if p.get("tracker") == "z" and p.get("transform") == "explode"]
    assert len(matches) == 1
    assert "kaboom" in matches[0]["error"]


def test_independent_branches_continue_when_sibling_fails(tmp_root):
    """Two transforms both depending only on raw; one fails, the other still runs."""
    schema = textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS raw (id INTEGER PRIMARY KEY, val INTEGER);
        CREATE TABLE IF NOT EXISTS good (source_id INTEGER PRIMARY KEY, v INTEGER);
        CREATE TABLE IF NOT EXISTS bad (source_id INTEGER PRIMARY KEY);
    """).strip()

    ingest = textwrap.dedent("""
        from personal_db.transforms import transform

        def sync(t):
            t.upsert("raw", [{"id": 1, "val": 7}], key=["id"])

        @transform(writes="good", depends_on=["raw"])
        def good(t, ctx):
            ctx.enrich(source="raw", target="good", fn=lambda r: {"v": r["val"]})

        @transform(writes="bad", depends_on=["raw"])
        def bad(t, ctx):
            raise RuntimeError("planned failure")
    """).strip()

    manifest = textwrap.dedent("""
        name: w
        description: t
        permission_type: none
        time_column: ts
        granularity: event
        schema:
          tables:
            raw:
              columns:
                id: {type: INTEGER, semantic: pk}
    """).strip()

    _write_tracker(tmp_root, "w", schema, ingest, manifest)
    cfg = Config(root=tmp_root)
    sync_one(cfg, "w")

    con = sqlite3.connect(cfg.db_path)
    # `good` ran successfully even though `bad` failed
    assert con.execute("SELECT v FROM good WHERE source_id=1").fetchone() == (7,)
    # `bad` produced nothing
    assert con.execute("SELECT count(*) FROM bad").fetchone()[0] == 0


def test_downstream_transform_skipped_when_dep_fails(tmp_root):
    """If A fails, B (which depends on A's output) should be skipped this tick."""
    schema = textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS raw (id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS mid (source_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS final (source_id INTEGER PRIMARY KEY);
    """).strip()

    ingest = textwrap.dedent("""
        from personal_db.transforms import transform

        def sync(t):
            t.upsert("raw", [{"id": 1}], key=["id"])

        @transform(writes="mid", depends_on=["raw"])
        def step_a(t, ctx):
            raise RuntimeError("upstream broke")

        @transform(writes="final", depends_on=["mid"])
        def step_b(t, ctx):
            ctx.con.execute("INSERT INTO final (source_id) VALUES (1)")
            ctx.con.commit()
    """).strip()

    manifest = textwrap.dedent("""
        name: chain
        description: t
        permission_type: none
        time_column: ts
        granularity: event
        schema:
          tables:
            raw:
              columns:
                id: {type: INTEGER, semantic: pk}
    """).strip()

    _write_tracker(tmp_root, "chain", schema, ingest, manifest)
    cfg = Config(root=tmp_root)
    sync_one(cfg, "chain")

    con = sqlite3.connect(cfg.db_path)
    assert con.execute("SELECT count(*) FROM mid").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM final").fetchone()[0] == 0  # skipped
```

- [ ] **Step 2: Run tests to verify they pass (Task 9 already implements this)**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 39 passed. If any of the three new tests fail, Task 9's `_run_transforms` has a bug — fix it (do not fix the tests).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_transforms.py
git commit -m "test(transforms): lock in error-isolation contract"
```

---

## Task 11: `sync.py` — run transforms after `backfill_one` too

**Files:**
- Modify: `src/personal_db/sync.py` (one-line addition to `backfill_one`)
- Test: `tests/unit/test_transforms.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_transforms.py`:

```python
from personal_db.sync import backfill_one


def test_backfill_one_also_runs_transforms(tmp_root):
    schema = textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS raw (id INTEGER PRIMARY KEY, val INTEGER);
        CREATE TABLE IF NOT EXISTS enriched (source_id INTEGER PRIMARY KEY, d INTEGER);
    """).strip()

    ingest = textwrap.dedent("""
        from personal_db.transforms import transform

        def sync(t):
            pass

        def backfill(t, start, end):
            t.upsert("raw", [{"id": 1, "val": 5}, {"id": 2, "val": 6}], key=["id"])

        @transform(writes="enriched", depends_on=["raw"])
        def enrich_them(t, ctx):
            ctx.enrich(source="raw", target="enriched", fn=lambda r: {"d": r["val"] * 10})
    """).strip()

    manifest = textwrap.dedent("""
        name: bf
        description: t
        permission_type: none
        time_column: ts
        granularity: event
        schema:
          tables:
            raw:
              columns:
                id: {type: INTEGER, semantic: pk}
    """).strip()

    _write_tracker(tmp_root, "bf", schema, ingest, manifest)
    cfg = Config(root=tmp_root)
    backfill_one(cfg, "bf", start=None, end=None)

    con = sqlite3.connect(cfg.db_path)
    rows = con.execute("SELECT source_id, d FROM enriched ORDER BY source_id").fetchall()
    assert rows == [(1, 50), (2, 60)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py::test_backfill_one_also_runs_transforms -v`
Expected: FAIL — `enriched` is empty because backfill doesn't trigger transforms.

- [ ] **Step 3: Add `_run_transforms` call to `backfill_one`**

In `src/personal_db/sync.py`, modify `backfill_one`:

```python
def backfill_one(cfg: Config, name: str, start: str | None, end: str | None) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.backfill(t, start, end)
    _run_transforms(cfg, name, mod, tracker_dir)   # NEW
    _store_horizon(cfg, name, manifest)
```

- [ ] **Step 4: Run tests to verify everything passes**

Run: `.venv/bin/python -m pytest tests/unit/test_transforms.py -v`
Expected: 40 passed.

Run the whole unit suite:
Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: full green; no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/sync.py tests/unit/test_transforms.py
git commit -m "feat(sync): run @transforms after backfill_one too"
```

---

## Final verification

- [ ] **Run all tests:** `.venv/bin/python -m pytest tests/ -q`
- [ ] **Lint:** `.venv/bin/ruff check src/personal_db/transforms.py src/personal_db/sync.py tests/unit/test_transforms.py`
- [ ] **Type check (if pyright is set up):** `.venv/bin/pyright src/personal_db/transforms.py`
- [ ] **Smoke test against a real tracker:** install any existing tracker (e.g. `personal-db --root /tmp/pdb_smoke tracker install habits`), confirm `personal-db --root /tmp/pdb_smoke sync habits` still works (no transforms declared, so the new code path is a no-op).

---

## What this plan does NOT cover (deferred per spec non-goals)

- Cross-tracker transforms.
- Per-transform `schedule.every`.
- A `personal-db transform run <tracker>:<name>` CLI.
- DAG visualization (`personal-db tracker dag <name>`).
- Migrating `daily_time_accounting` to use the new framework — it stays hand-rolled.
- Test-framework / freshness-assertion features.

These are explicitly out of scope and should be tracked as separate plans if/when needed.
