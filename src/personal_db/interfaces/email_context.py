"""Email-context provider capability contract.

`enrichments.finance` and the core `email_search_receipts`/`email_read_thread`
MCP tools depend on the `EmailContextProvider` Protocol here instead of
importing a concrete provider (e.g. SparkEmailContextProvider) directly.
Concrete providers are resolved by name via `core.providers.
resolve_email_context_provider`.

`EvidenceRef`/`ContextResult` used to live in `context_providers/base.py`;
they moved here (and that module was deleted) because they are the shared
vocabulary of the capability contract itself, not a context-provider
implementation detail — the generic enrichment queue (`core.enrichment_queue`,
core code) also needs `EvidenceRef` to record provenance, and core is allowed
to depend on `interfaces` (interfaces sit below core in the import-linter
layers contract) but not on concrete context-provider/remote-source packages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


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


@runtime_checkable
class EmailContextProvider(Protocol):
    """Capability contract for semantic email context lookups.

    Mirrors exactly the subset of SparkEmailContextProvider's surface that
    enrichments.finance (jobs.py, receipt_debug.py, receipt_matching.py) and
    the core email MCP tools actually call.
    """

    def search_receipts(
        self,
        *,
        merchant: str | None = None,
        amount: Any = None,
        date_: str | None = None,
        window_days: int = 7,
        scope: str | None = None,
    ) -> ContextResult:
        """Find receipt-like email candidates for a transaction."""
        ...

    def read_thread(
        self,
        message_id: str,
        *,
        download_attachments: bool = False,
    ) -> ContextResult:
        """Read a full email thread by provider-specific message id."""
        ...
