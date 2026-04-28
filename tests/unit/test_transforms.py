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
