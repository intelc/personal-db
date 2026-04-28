import sqlite3

import pytest

from personal_db.config import Config
from personal_db.db import init_db
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
