from __future__ import annotations

import re

from fastapi import HTTPException

_TRACKER_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def validate_name(name: str) -> None:
    if not _TRACKER_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"invalid tracker name: {name!r}")
