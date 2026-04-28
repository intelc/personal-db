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
