import pytest

from personal_db.transforms import TransformError, TransformSpec, topo_sort, transform, validate


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
    # "mid" is NOT in schema — it must be satisfied by transform a's output
    validate([a, b], schema_tables={"raw", "final"})


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
