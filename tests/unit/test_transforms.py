import json as _json
import sqlite3
import textwrap
from pathlib import Path

import pytest

from personal_db.config import Config
from personal_db.db import init_db
from personal_db.sync import backfill_one, sync_one
from personal_db.tracker import Tracker
from personal_db.transforms import (
    TransformContext,
    TransformError,
    TransformSpec,
    _detect_pk,
    make_context,
    topo_sort,
    transform,
    validate,
)


def _spec(name: str, writes: str, depends_on: list[str]) -> TransformSpec:
    """Build a spec without needing a real function."""
    return TransformSpec(name=name, fn=lambda t, ctx: None, writes=writes, depends_on=depends_on)


def _write_tracker(tmp_root: Path, name: str, schema: str, ingest: str, manifest: str) -> Path:
    """Create a fake tracker dir under tmp_root/trackers/<name>/."""
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "schema.sql").write_text(schema)
    (d / "ingest.py").write_text(ingest)
    (d / "manifest.yaml").write_text(manifest)
    return d


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


def test_detect_pk_when_pk_is_not_first_column():
    """Verify _detect_pk filters by pk>0, not just position 0."""
    con = _make_db("CREATE TABLE t (val INTEGER, name TEXT, uuid TEXT PRIMARY KEY)")
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


# ---------------------------------------------------------------------------
# Task 6: enrich — basic (no dedup, no batching)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Task 7: enrich — content-addressed dedup cache
# ---------------------------------------------------------------------------


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
        source="raw",
        target="enriched",
        fn=lambda r: {"doubled": r["val"] * 2},
        dedup_key=lambda r: str(r["val"]),
    )

    con = sqlite3.connect(cfg.db_path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "_enriched_cache" in tables
    rows = con.execute("SELECT key, value FROM _enriched_cache").fetchall()
    assert rows == [("10", '{"doubled": 20}')]


# ---------------------------------------------------------------------------
# Task 8: enrich — per-batch atomic commits + failure recovery
# ---------------------------------------------------------------------------


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


def test_enrich_target_writes_rolled_back_when_batch_fails_mid_batch(tmp_root):
    """Rows 1-2 of a 4-row batch must NOT persist to target if row 3 fails."""
    _setup_db_with_source_table(
        tmp_root,
        [{"id": i, "val": i * 10} for i in range(1, 5)],  # 4 rows: ids 1-4
    )
    cfg = Config(root=tmp_root)
    t = Tracker(name="tt", cfg=cfg, manifest=None)
    ctx = make_context(t, _spec("d", "enriched", ["raw"]))

    def fn(row):
        if row["id"] == 3:
            raise RuntimeError("boom")
        return {"doubled": row["val"] * 2}

    with pytest.raises(RuntimeError, match="boom"):
        ctx.enrich(source="raw", target="enriched", fn=fn, batch_size=4)

    # Rows 1 and 2 were processed inside the same batch as the failed row 3.
    # Per-batch atomicity: they must be rolled back, NOT persisted.
    con = sqlite3.connect(cfg.db_path)
    rows = con.execute("SELECT source_id FROM enriched").fetchall()
    assert rows == [], f"expected empty target after mid-batch failure, got {rows}"

    # Cursor should NOT have advanced (no batch committed).
    ctx2 = make_context(t, _spec("d", "enriched", ["raw"]))
    assert ctx2.cursor.get() in (None, "")


# ---------------------------------------------------------------------------
# Task 9: sync_one integration — _run_transforms called after ingest
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Task 10: error isolation contract tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Task 11: backfill_one integration — _run_transforms called after backfill
# ---------------------------------------------------------------------------


def test_backfill_one_also_runs_transforms(tmp_root):
    schema = textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS raw (id INTEGER PRIMARY KEY, val INTEGER);
        CREATE TABLE IF NOT EXISTS enriched (source_id INTEGER PRIMARY KEY, doubled INTEGER);
    """).strip()

    ingest = textwrap.dedent("""
        from personal_db.tracker import Tracker
        from personal_db.transforms import transform

        def backfill(t: Tracker, start, end) -> None:
            t.upsert("raw", [{"id": 1, "val": 5}, {"id": 2, "val": 10}], key=["id"])

        def sync(t: Tracker) -> None:
            pass

        @transform(writes="enriched", depends_on=["raw"])
        def double_them(t, ctx):
            ctx.enrich(source="raw", target="enriched", fn=lambda r: {"doubled": r["val"] * 2})
    """).strip()

    manifest = textwrap.dedent("""
        name: bf
        description: backfill transform test
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

    _write_tracker(tmp_root, "bf", schema, ingest, manifest)

    cfg = Config(root=tmp_root)
    backfill_one(cfg, "bf", start=None, end=None)

    con = sqlite3.connect(cfg.db_path)
    raw = con.execute("SELECT id, val FROM raw ORDER BY id").fetchall()
    enriched = con.execute("SELECT source_id, doubled FROM enriched ORDER BY source_id").fetchall()

    assert raw == [(1, 5), (2, 10)]
    assert enriched == [(1, 10), (2, 20)]
