from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceRef:
    """Reference to source material used or returned by a context provider."""

    source: str
    ref: str
    kind: str
    title: str | None = None
    excerpt: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ref": self.ref,
            "kind": self.kind,
            "title": self.title,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class ContextResult:
    """Structured context-provider output with auditable evidence refs."""

    provider: str
    operation: str
    query: dict[str, Any]
    evidence: list[EvidenceRef] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "operation": self.operation,
            "query": self.query,
            "evidence": [ref.as_dict() for ref in self.evidence],
            "data": self.data,
            "raw_text": self.raw_text,
        }
