"""life_context — manual-capture diary tracker.

There is no automatic ingest source. Entries are written via the
`log_life_context` MCP tool (or the lower-level `log_event` tool, if you
want to insert a single row without range fan-out).

The sync function is a no-op kept only so the framework's contract holds.
"""

from __future__ import annotations

from personal_db.tracker import Tracker


def sync(t: Tracker) -> None:
    # Nothing to ingest — entries arrive via log_life_context MCP tool.
    t.log.info("life_context: no-op sync (manual capture only)")


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)
