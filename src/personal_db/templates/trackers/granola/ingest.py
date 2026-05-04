"""Granola tracker — pulls meeting docs and transcripts.

Auth: reads the access token directly from the Granola desktop app's local
supabase.json on every sync. We do not refresh; if the token is stale, the
user must open the Granola desktop app to refresh it.
"""

from __future__ import annotations

import json
from pathlib import Path

from personal_db.tracker import Tracker

SUPABASE_PATH = Path.home() / "Library/Application Support/Granola/supabase.json"


def _extract_workos_access_token(node) -> str | None:
    """Walk a JSON tree looking for workos_tokens.access_token.

    `workos_tokens` may be a JSON-encoded string or a dict — handle both.
    Returns the first non-empty access_token found, or None.
    """
    if isinstance(node, dict):
        wt = node.get("workos_tokens")
        if isinstance(wt, str):
            try:
                wt = json.loads(wt)
            except json.JSONDecodeError:
                wt = None
        if isinstance(wt, dict):
            tok = wt.get("access_token") or ""
            if tok:
                return tok
        for value in node.values():
            tok = _extract_workos_access_token(value)
            if tok:
                return tok
    elif isinstance(node, list):
        for item in node:
            tok = _extract_workos_access_token(item)
            if tok:
                return tok
    return None


def _read_access_token(path: Path = SUPABASE_PATH) -> str:
    """Read the current Granola access token from the desktop app's local store.

    Raises RuntimeError with a user-facing instruction when the file is missing
    or no token can be extracted.
    """
    if not path.exists():
        raise RuntimeError(
            f"Granola desktop app not detected at {path}. "
            "Install Granola and sign in."
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Granola supabase.json is not valid JSON: {e}") from e

    token = _extract_workos_access_token(data)
    if not token:
        raise RuntimeError(
            "Granola access token not found in supabase.json. "
            "Sign in to the Granola desktop app."
        )
    return token


def sync(t: Tracker) -> None:
    raise NotImplementedError


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    raise NotImplementedError
