from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class RemoteCallResult:
    """Result returned by a remote source call.

    `raw_text` is intentionally preserved. Some remote tools expose rich,
    human-oriented output before they expose stable JSON; callers can still use
    the structured fields that personal_db knows how to parse today.
    """

    source: str
    operation: str
    raw_text: str
    data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "operation": self.operation,
            "data": self.data,
            "raw_text": self.raw_text,
        }


class RemoteSource(Protocol):
    name: str

    def check(self) -> RemoteCallResult:
        """Return availability/configuration information for this source."""
