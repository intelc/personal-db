"""Granola tracker — pulls meeting docs and transcripts.

Auth: reads the access token directly from the Granola desktop app's local
supabase.json on every sync. We do not refresh; if the token is stale, the
user must open the Granola desktop app to refresh it.
"""

from personal_db.tracker import Tracker


def sync(t: Tracker) -> None:
    raise NotImplementedError


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    raise NotImplementedError
