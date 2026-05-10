"""Withings smart-scale ingest.

Pulls body-composition measurements (weight, fat %, fat mass, lean/muscle/
bone mass, hydration, heart pulse) via the Measure v2 API. Uses the
Withings `lastupdate` parameter as a cursor so corrections to historical
weigh-ins are picked up on the next sync.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import requests

from personal_db.oauth import refresh_if_needed
from personal_db.tracker import Cursor, Tracker

MEASURE_URL = "https://wbsapi.withings.net/measure"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"

# Withings measure types we store. Anything else in the response is ignored.
TYPE_MAP: dict[int, str] = {
    1:  "weight_kg",
    5:  "lean_mass_kg",
    6:  "fat_ratio_pct",
    8:  "fat_mass_kg",
    11: "heart_pulse_bpm",
    76: "muscle_mass_kg",
    77: "hydration_kg",
    88: "bone_mass_kg",
}

# Comma-separated CSV passed to the API so we only fetch what we store.
MEAS_TYPES_CSV = ",".join(str(k) for k in TYPE_MAP.keys())


def _client_credentials() -> tuple[str, str]:
    cid = os.environ.get("WITHINGS_CLIENT_ID")
    cs = os.environ.get("WITHINGS_CLIENT_SECRET")
    if not cid or not cs:
        raise RuntimeError("Set WITHINGS_CLIENT_ID and WITHINGS_CLIENT_SECRET")
    return cid, cs


def _iso_utc(unix_seconds: int | None) -> str | None:
    if unix_seconds is None:
        return None
    return datetime.fromtimestamp(int(unix_seconds), tz=UTC).isoformat()


def _flatten(grp: dict, default_tz: str) -> dict[str, Any]:
    """Convert one Withings measuregrp into a withings_measurements row.

    The `_modified_unix` field is internal — it's used to compute the
    cursor max in sync(); it must be popped from each row before upsert.
    """
    row: dict[str, Any] = {
        "grpid": str(grp["grpid"]),
        "date": _iso_utc(grp["date"]),
        "timezone": grp.get("timezone") or default_tz,
        "attrib": grp.get("attrib"),
        "category": grp.get("category"),
        "device_id": grp.get("deviceid"),
        "created_at": _iso_utc(grp.get("created")),
        "modified_at": _iso_utc(grp.get("modified")),
        "_modified_unix": int(grp.get("modified") or grp["date"]),
        "weight_kg": None,
        "fat_ratio_pct": None,
        "fat_mass_kg": None,
        "lean_mass_kg": None,
        "muscle_mass_kg": None,
        "bone_mass_kg": None,
        "hydration_kg": None,
        "heart_pulse_bpm": None,
    }
    for m in grp.get("measures") or []:
        col = TYPE_MAP.get(m["type"])
        if not col:
            continue
        unit = m["unit"]
        scaled = round(m["value"] * (10 ** unit), abs(unit))
        row[col] = int(scaled) if col == "heart_pulse_bpm" else float(scaled)
    return row


def _fetch_measures(
    access_token: str,
    *,
    lastupdate: str | None,
    offset: int,
) -> dict:
    """One getmeas call. Returns the response `body` dict (envelope-unwrapped).
    Raises RuntimeError on non-zero Withings status."""
    params: dict[str, Any] = {
        "action": "getmeas",
        "meastypes": MEAS_TYPES_CSV,
        "category": 1,
        "offset": offset,
    }
    if lastupdate:
        params["lastupdate"] = lastupdate
    r = requests.post(
        MEASURE_URL,
        data=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    r.raise_for_status()
    envelope = r.json()
    if envelope.get("status") != 0:
        raise RuntimeError(f"Withings measure error: status={envelope.get('status')} body={envelope}")
    return envelope.get("body") or {}


def sync(t: Tracker) -> None:
    raise NotImplementedError("filled in Task 11")


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    raise NotImplementedError("filled in Task 11")
