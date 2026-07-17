"""Shared parsing for `every`-style interval strings (e.g. "10m", "1h", "7d").

Used by tracker `schedule.every` (core.sync) and declared background jobs'
`every` field (core.background_jobs) so both cadence mechanisms agree on one
format.
"""

from __future__ import annotations

import re
from datetime import timedelta

_EVERY_RE = re.compile(r"^(\d+)\s*([smhd])$")


def parse_every(value: str) -> timedelta:
    m = _EVERY_RE.match(value.strip())
    if not m:
        raise ValueError(f"bad every-interval: {value!r}")
    n, unit = int(m.group(1)), m.group(2)
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]
