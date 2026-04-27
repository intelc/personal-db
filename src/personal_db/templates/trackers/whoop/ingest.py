import os
import sqlite3

import requests

from personal_db.db import connect
from personal_db.oauth import refresh_if_needed
from personal_db.tracker import Cursor, Tracker

WHOOP_API = "https://api.prod.whoop.com/developer"

# Columns added to whoop_cycles in the v2 schema.  Any that already exist are
# silently skipped, so this file is safe to run on both fresh and existing DBs.
_CYCLES_NEW_COLS = [
    ("timezone_offset", "TEXT"),
    ("score_state", "TEXT"),
    ("kilojoule", "REAL"),
    ("max_heart_rate", "INTEGER"),
]


def _migrate_schema(db_path) -> None:
    """Add new columns to whoop_cycles if they are missing (idempotent)."""
    con = sqlite3.connect(db_path)
    try:
        existing = {row[1] for row in con.execute("PRAGMA table_info(whoop_cycles)")}
        for col, col_type in _CYCLES_NEW_COLS:
            if col not in existing:
                con.execute(f"ALTER TABLE whoop_cycles ADD COLUMN {col} {col_type}")
        con.commit()
    finally:
        con.close()


def _client_credentials():
    cid = os.environ.get("WHOOP_CLIENT_ID")
    cs = os.environ.get("WHOOP_CLIENT_SECRET")
    if not cid or not cs:
        raise RuntimeError("Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET")
    return cid, cs


def _auth_headers(cfg, cid: str, cs: str) -> dict:
    token = refresh_if_needed(
        cfg,
        "whoop",
        token_url=f"{WHOOP_API}/oauth/oauth2/token",
        client_id=cid,
        client_secret=cs,
    )
    return {"Authorization": f"Bearer {token['access_token']}"}


def _fetch_paginated(headers: dict, path: str, cursor_start: str | None) -> list[dict]:
    """Fetch all pages from a paginated Whoop endpoint and return raw records."""
    params: dict = {"limit": 25}
    if cursor_start:
        params["start"] = cursor_start
    records: list[dict] = []
    next_token = None
    while True:
        if next_token:
            params["nextToken"] = next_token
        r = requests.get(f"{WHOOP_API}{path}", headers=headers, params=params, timeout=15)
        r.raise_for_status()
        body = r.json()
        records.extend(body.get("records", []))
        next_token = body.get("next_token")
        if not next_token:
            break
    return records


# ---------------------------------------------------------------------------
# Flatten helpers
# ---------------------------------------------------------------------------


def _flatten_cycle(rec: dict) -> dict:
    score = rec.get("score") or {}
    return {
        "id": str(rec["id"]),
        "start": rec["start"],
        "end": rec.get("end"),
        "timezone_offset": rec.get("timezone_offset"),
        "score_state": rec.get("score_state"),
        "strain": score.get("strain"),
        "kilojoule": score.get("kilojoule"),
        "average_heart_rate": score.get("average_heart_rate"),
        "max_heart_rate": score.get("max_heart_rate"),
    }


def _flatten_recovery(rec: dict, cycle_start_by_id: dict) -> dict:
    score = rec.get("score") or {}
    cycle_id = str(rec["cycle_id"])
    return {
        "cycle_id": cycle_id,
        "sleep_id": str(rec["sleep_id"]) if rec.get("sleep_id") is not None else None,
        "start": cycle_start_by_id.get(cycle_id),
        "score_state": rec.get("score_state"),
        "recovery_score": score.get("recovery_score"),
        "resting_heart_rate": score.get("resting_heart_rate"),
        "hrv_rmssd_milli": score.get("hrv_rmssd_milli"),
        "spo2_percentage": score.get("spo2_percentage"),
        "skin_temp_celsius": score.get("skin_temp_celsius"),
    }


def _flatten_sleep(rec: dict) -> dict:
    score = rec.get("score") or {}
    stage = score.get("stage_summary") or {}
    return {
        "id": str(rec["id"]),
        "start": rec.get("start"),
        "end": rec.get("end"),
        "timezone_offset": rec.get("timezone_offset"),
        "nap": 1 if rec.get("nap") else 0,
        "score_state": rec.get("score_state"),
        "total_in_bed_milli": stage.get("total_in_bed_time_milli"),
        "total_awake_milli": stage.get("total_awake_time_milli"),
        "total_light_sleep_milli": stage.get("total_light_sleep_time_milli"),
        "total_slow_wave_sleep_milli": stage.get("total_slow_wave_sleep_time_milli"),
        "total_rem_sleep_milli": stage.get("total_rem_sleep_time_milli"),
        "sleep_cycle_count": stage.get("sleep_cycle_count"),
        "disturbance_count": stage.get("disturbance_count"),
        "respiratory_rate": score.get("respiratory_rate"),
        "sleep_performance_pct": score.get("sleep_performance_percentage"),
        "sleep_consistency_pct": score.get("sleep_consistency_percentage"),
        "sleep_efficiency_pct": score.get("sleep_efficiency_percentage"),
    }


def _flatten_workout(rec: dict) -> dict:
    score = rec.get("score") or {}
    zones = score.get("zone_duration") or score.get("zone_durations") or {}
    return {
        "id": str(rec["id"]),
        "start": rec.get("start"),
        "end": rec.get("end"),
        "timezone_offset": rec.get("timezone_offset"),
        "sport_id": rec.get("sport_id"),
        "score_state": rec.get("score_state"),
        "strain": score.get("strain"),
        "average_heart_rate": score.get("average_heart_rate"),
        "max_heart_rate": score.get("max_heart_rate"),
        "kilojoule": score.get("kilojoule"),
        "percent_recorded": score.get("percent_recorded"),
        "distance_meter": score.get("distance_meter"),
        "altitude_gain_meter": score.get("altitude_gain_meter"),
        "altitude_change_meter": score.get("altitude_change_meter"),
        "zone_zero_milli": zones.get("zone_zero_milli"),
        "zone_one_milli": zones.get("zone_one_milli"),
        "zone_two_milli": zones.get("zone_two_milli"),
        "zone_three_milli": zones.get("zone_three_milli"),
        "zone_four_milli": zones.get("zone_four_milli"),
        "zone_five_milli": zones.get("zone_five_milli"),
    }


# ---------------------------------------------------------------------------
# Per-resource sync functions
# ---------------------------------------------------------------------------


def _sync_cycles(t: Tracker, headers: dict) -> None:
    cursor = Cursor("whoop:cycles", t.cfg.state_dir)
    records = _fetch_paginated(headers, "/v2/cycle", cursor.get())
    rows = [_flatten_cycle(r) for r in records]
    if rows:
        t.upsert("whoop_cycles", rows, key=["id"])
        cursor.set(max(r["start"] for r in rows))
    t.log.info("whoop cycles: %d", len(rows))


def _sync_recovery(t: Tracker, headers: dict) -> None:
    cursor = Cursor("whoop:recovery", t.cfg.state_dir)
    records = _fetch_paginated(headers, "/v2/recovery", cursor.get())
    if not records:
        t.log.info("whoop recovery: 0")
        return

    # Denormalize cycle start from whoop_cycles for time-series queries.
    cycle_ids = [str(r["cycle_id"]) for r in records]
    placeholders = ",".join("?" * len(cycle_ids))
    con = connect(t.cfg.db_path)
    cycle_rows = con.execute(
        f"SELECT id, start FROM whoop_cycles WHERE id IN ({placeholders})",
        cycle_ids,
    ).fetchall()
    con.close()
    cycle_start_by_id = {row[0]: row[1] for row in cycle_rows}

    rows = [_flatten_recovery(r, cycle_start_by_id) for r in records]
    t.upsert("whoop_recovery", rows, key=["cycle_id"])
    # Use the denormalized start for cursor when available; fall back to the
    # latest non-None value or leave cursor unchanged.
    starts = [r["start"] for r in rows if r["start"]]
    if starts:
        cursor.set(max(starts))
    t.log.info("whoop recovery: %d", len(rows))


def _sync_sleep(t: Tracker, headers: dict) -> None:
    cursor = Cursor("whoop:sleep", t.cfg.state_dir)
    records = _fetch_paginated(headers, "/v2/activity/sleep", cursor.get())
    rows = [_flatten_sleep(r) for r in records]
    if rows:
        t.upsert("whoop_sleep", rows, key=["id"])
        starts = [r["start"] for r in rows if r["start"]]
        if starts:
            cursor.set(max(starts))
    t.log.info("whoop sleep: %d", len(rows))


def _sync_workouts(t: Tracker, headers: dict) -> None:
    cursor = Cursor("whoop:workouts", t.cfg.state_dir)
    records = _fetch_paginated(headers, "/v2/activity/workout", cursor.get())
    rows = [_flatten_workout(r) for r in records]
    if rows:
        t.upsert("whoop_workouts", rows, key=["id"])
        starts = [r["start"] for r in rows if r["start"]]
        if starts:
            cursor.set(max(starts))
    t.log.info("whoop workouts: %d", len(rows))


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    _migrate_schema(t.cfg.db_path)
    cid, cs = _client_credentials()
    headers = _auth_headers(t.cfg, cid, cs)
    _sync_cycles(t, headers)
    _sync_recovery(t, headers)
    _sync_sleep(t, headers)
    _sync_workouts(t, headers)
