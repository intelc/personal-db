"""Within-tracker transformation layer.

A tracker's `ingest.py` can declare zero or more transforms — derived tables
computed from the tracker's own raw tables (or other transforms in the same
tracker). The framework discovers them by walking module attributes for the
`_transform_spec` attribute attached by `@transform`, topo-sorts by their
declared (writes, depends_on) edges, and runs them in order after `sync()`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


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

    # Rule 1: writes target must be in schema_tables OR consumed as a dep by
    # another transform (i.e. it is an intermediate table in the pipeline).
    all_deps = {d for s in specs for d in s.depends_on}
    for s in specs:
        if s.writes not in schema_tables and s.writes not in all_deps:
            raise TransformError(
                f"transform '{s.name}' writes to '{s.writes}' "
                f"which is not declared in schema.sql and is not "
                f"consumed by any other transform"
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
