"""Shared email-context-provider resolution for finance enrichments.

jobs.py, receipt_debug.py, and receipt_matching.py all accept an optional
`provider: EmailContextProvider` override (used by tests to inject a fake);
when not given, they resolve one via `core.providers.
resolve_email_context_provider` and degrade with a clear error instead of an
import failure when nothing is configured.
"""

from __future__ import annotations

from personal_db.core.config import Config
from personal_db.core.providers import resolve_email_context_provider
from personal_db.interfaces.email_context import EmailContextProvider


class NoEmailContextProviderConfigured(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "no email context provider configured; set config.yaml "
            "providers.email_context (or install the spark_email source)"
        )


def require_email_context_provider(
    cfg: Config,
    provider: EmailContextProvider | None,
) -> EmailContextProvider:
    resolved = provider or resolve_email_context_provider(cfg)
    if resolved is None:
        raise NoEmailContextProviderConfigured()
    return resolved
