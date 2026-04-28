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
