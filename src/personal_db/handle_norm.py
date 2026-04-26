"""Phone/email handle normalization, shared between contacts ingest and any
consumer (e.g. imessage viz) that wants to join on handles.

Strategy:
  - Email: strip whitespace, lowercase. Trivial.
  - Phone: digits-only, take last 10. US-centric but works for matching most
    real-world data: "+1 (408) 921-5283" and "4089215283" both → "4089215283".
    For non-US numbers (+44...) the last 10 still match if both sides drop
    country codes the same way; if both keep them, exact match still works.

Use the same function on both sides of the join — that's what makes it work.
"""

from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D")


def normalize_handle(handle: str | None) -> str:
    """Normalize a phone-or-email handle for cross-table matching.

    Returns "" for None / empty input. The output is the form to store in
    `contact_handles.normalized` and to compute on iMessage's `handle` column
    at query time.
    """
    if not handle:
        return ""
    h = handle.strip()
    if "@" in h:
        return h.lower()
    digits = _DIGITS_RE.sub("", h)
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def handle_kind(handle: str | None) -> str:
    """Classify a raw handle as 'email' or 'phone'. Defaults to 'phone'."""
    if handle and "@" in handle:
        return "email"
    return "phone"
