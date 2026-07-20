from __future__ import annotations

import re

from fastapi import HTTPException

_TRACKER_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def validate_name(name: str) -> None:
    if not _TRACKER_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"invalid tracker name: {name!r}")


# Slug rule for scaffolding a brand-new custom tracker: lowercase, starts
# with a letter, digits/underscores after, 2-32 chars total. Stricter than
# `validate_name` above (which just guards path traversal on an *existing*
# tracker) -- this one also has to read well as a Python module stem
# (ingest.py imports it implicitly) and a SQL table-name prefix. Shared by
# routes/setup.py's "Add your own source" form and routes/agent.py's
# connector-prompt route.
NEW_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
