"""Oura Ring v2 connector — daily activity, readiness, sleep, stress, SpO2,
sleep periods, workouts, sessions, heart rate.

Auth: OAuth2. Setup wires OURA_CLIENT_ID / OURA_CLIENT_SECRET and runs the
shared OAuth flow against https://cloud.ouraring.com/oauth/authorize. Token is
refreshed automatically via personal_db.oauth.refresh_if_needed.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from personal_db.oauth import refresh_if_needed
from personal_db.tracker import Cursor, Tracker

OURA_API = "https://api.ouraring.com/v2/usercollection"
OURA_TOKEN_URL = "https://api.ouraring.com/oauth/token"

DEFAULT_RECENT_DAYS = 14  # cushion when we already have a cursor
INITIAL_BACKFILL_DAYS = 365


def _client_credentials() -> tuple[str, str]:
    cid = os.environ.get("OURA_CLIENT_ID")
    cs = os.environ.get("OURA_CLIENT_SECRET")
    if not cid or not cs:
        raise RuntimeError("Set OURA_CLIENT_ID and OURA_CLIENT_SECRET")
    return cid, cs


def _auth_headers(cfg) -> dict[str, str]:
    cid, cs = _client_credentials()
    token = refresh_if_needed(
        cfg, "oura", token_url=OURA_TOKEN_URL, client_id=cid, client_secret=cs
    )
    return {"Authorization": f"Bearer {token['access_token']}"}


def _today_iso_date() -> str:
    return date.today().isoformat()


def _today_iso_datetime() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_minus(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _datetime_minus(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _resolve_date_window(t: Tracker, key: str) -> tuple[str, str]:
    cursor = Cursor(key, t.cfg.state_dir).get()
    if cursor:
        try:
            since = (date.fromisoformat(cursor) - timedelta(days=2)).isoformat()
        except ValueError:
            since = _date_minus(DEFAULT_RECENT_DAYS)
    else:
        since = _date_minus(INITIAL_BACKFILL_DAYS)
    return since, _today_iso_date()


def _resolve_datetime_window(t: Tracker, key: str) -> tuple[str, str]:
    cursor = Cursor(key, t.cfg.state_dir).get()
    if cursor:
        try:
            since_dt = datetime.fromisoformat(cursor) - timedelta(hours=6)
        except ValueError:
            since_dt = datetime.now(timezone.utc) - timedelta(days=DEFAULT_RECENT_DAYS)
        since = since_dt.isoformat()
    else:
        since = _datetime_minus(INITIAL_BACKFILL_DAYS)
    return since, _today_iso_datetime()


def _fetch_paginated(
    headers: dict[str, str], path: str, params: dict[str, Any]
) -> list[dict]:
    records: list[dict] = []
    next_token: str | None = None
    while True:
        page_params = dict(params)
        if next_token:
            page_params["next_token"] = next_token
        r = requests.get(
            f"{OURA_API}{path}", headers=headers, params=page_params, timeout=20
        )
        r.raise_for_status()
        body = r.json()
        records.extend(body.get("data", []))
        next_token = body.get("next_token")
        if not next_token:
            break
    return records


# --- flatteners -------------------------------------------------------------


def _flat_daily_activity(r: dict) -> dict:
    return {
        "id": r["id"],
        "day": r["day"],
        "score": r.get("score"),
        "active_calories": r.get("active_calories"),
        "total_calories": r.get("total_calories"),
        "steps": r.get("steps"),
        "equivalent_walking_distance": r.get("equivalent_walking_distance"),
        "high_activity_time": r.get("high_activity_time"),
        "medium_activity_time": r.get("medium_activity_time"),
        "low_activity_time": r.get("low_activity_time"),
        "sedentary_time": r.get("sedentary_time"),
        "resting_time": r.get("resting_time"),
        "non_wear_time": r.get("non_wear_time"),
        "high_activity_met_minutes": r.get("high_activity_met_minutes"),
        "medium_activity_met_minutes": r.get("medium_activity_met_minutes"),
        "low_activity_met_minutes": r.get("low_activity_met_minutes"),
        "inactivity_alerts": r.get("inactivity_alerts"),
        "meters_to_target": r.get("meters_to_target"),
        "target_calories": r.get("target_calories"),
        "target_meters": r.get("target_meters"),
        "average_met_minutes": r.get("average_met_minutes"),
        "timestamp": r.get("timestamp"),
    }


def _flat_daily_readiness(r: dict) -> dict:
    c = r.get("contributors") or {}
    return {
        "id": r["id"],
        "day": r["day"],
        "score": r.get("score"),
        "temperature_deviation": r.get("temperature_deviation"),
        "temperature_trend_deviation": r.get("temperature_trend_deviation"),
        "activity_balance": c.get("activity_balance"),
        "body_temperature": c.get("body_temperature"),
        "hrv_balance": c.get("hrv_balance"),
        "previous_day_activity": c.get("previous_day_activity"),
        "previous_night": c.get("previous_night"),
        "recovery_index": c.get("recovery_index"),
        "resting_heart_rate": c.get("resting_heart_rate"),
        "sleep_balance": c.get("sleep_balance"),
        "timestamp": r.get("timestamp"),
    }


def _flat_daily_sleep(r: dict) -> dict:
    c = r.get("contributors") or {}
    return {
        "id": r["id"],
        "day": r["day"],
        "score": r.get("score"),
        "contrib_deep_sleep": c.get("deep_sleep"),
        "contrib_efficiency": c.get("efficiency"),
        "contrib_latency": c.get("latency"),
        "contrib_rem_sleep": c.get("rem_sleep"),
        "contrib_restfulness": c.get("restfulness"),
        "contrib_timing": c.get("timing"),
        "contrib_total_sleep": c.get("total_sleep"),
        "timestamp": r.get("timestamp"),
    }


def _flat_daily_stress(r: dict) -> dict:
    return {
        "id": r["id"],
        "day": r["day"],
        "stress_high": r.get("stress_high"),
        "recovery_high": r.get("recovery_high"),
        "day_summary": r.get("day_summary"),
    }


def _flat_daily_spo2(r: dict) -> dict:
    pct = r.get("spo2_percentage") or {}
    return {
        "id": r["id"],
        "day": r["day"],
        "spo2_percentage_avg": pct.get("average"),
        "breathing_disturbance_index": r.get("breathing_disturbance_index"),
    }


def _flat_sleep(r: dict) -> dict:
    return {
        "id": r["id"],
        "day": r.get("day"),
        "bedtime_start": r.get("bedtime_start"),
        "bedtime_end": r.get("bedtime_end"),
        "type": r.get("type"),
        "period": r.get("period"),
        "total_sleep_duration": r.get("total_sleep_duration"),
        "time_in_bed": r.get("time_in_bed"),
        "awake_time": r.get("awake_time"),
        "light_sleep_duration": r.get("light_sleep_duration"),
        "deep_sleep_duration": r.get("deep_sleep_duration"),
        "rem_sleep_duration": r.get("rem_sleep_duration"),
        "efficiency": r.get("efficiency"),
        "latency": r.get("latency"),
        "restless_periods": r.get("restless_periods"),
        "average_breath": r.get("average_breath"),
        "average_heart_rate": r.get("average_heart_rate"),
        "lowest_heart_rate": r.get("lowest_heart_rate"),
        "average_hrv": r.get("average_hrv"),
        "readiness_score_delta": r.get("readiness_score_delta"),
        "sleep_score_delta": r.get("sleep_score_delta"),
        "low_battery_alert": 1 if r.get("low_battery_alert") else 0,
    }


def _flat_workout(r: dict) -> dict:
    return {
        "id": r["id"],
        "day": r.get("day"),
        "start_datetime": r.get("start_datetime"),
        "end_datetime": r.get("end_datetime"),
        "activity": r.get("activity"),
        "intensity": r.get("intensity"),
        "source": r.get("source"),
        "load": r.get("load"),
        "average_heart_rate": r.get("average_heart_rate"),
        "max_heart_rate": r.get("max_heart_rate"),
        "calories": r.get("calories"),
        "distance": r.get("distance"),
        "label": r.get("label"),
    }


def _flat_session(r: dict) -> dict:
    return {
        "id": r["id"],
        "day": r.get("day"),
        "start_datetime": r.get("start_datetime"),
        "end_datetime": r.get("end_datetime"),
        "type": r.get("type"),
        "mood": r.get("mood"),
    }


def _flat_heartrate(r: dict) -> dict:
    return {
        "timestamp": r.get("timestamp"),
        "bpm": r.get("bpm"),
        "source": r.get("source") or "unknown",
    }


# --- per-resource sync ------------------------------------------------------


def _sync_date_resource(
    t: Tracker,
    *,
    headers: dict[str, str],
    path: str,
    table: str,
    cursor_key: str,
    flatten,
) -> None:
    since, until = _resolve_date_window(t, cursor_key)
    records = _fetch_paginated(
        headers, path, {"start_date": since, "end_date": until}
    )
    rows = [flatten(r) for r in records if r.get("id") and r.get("day")]
    if rows:
        t.upsert(table, rows, key=["id"])
        Cursor(cursor_key, t.cfg.state_dir).set(max(r["day"] for r in rows))
    t.log.info("oura %s: %d", table, len(rows))


def _sync_datetime_resource(
    t: Tracker,
    *,
    headers: dict[str, str],
    path: str,
    table: str,
    cursor_key: str,
    flatten,
    cursor_field: str,
) -> None:
    since, until = _resolve_datetime_window(t, cursor_key)
    records = _fetch_paginated(
        headers, path, {"start_datetime": since, "end_datetime": until}
    )
    rows = [flatten(r) for r in records if r.get("id")]
    if rows:
        t.upsert(table, rows, key=["id"])
        timestamps = [r[cursor_field] for r in rows if r.get(cursor_field)]
        if timestamps:
            Cursor(cursor_key, t.cfg.state_dir).set(max(timestamps))
    t.log.info("oura %s: %d", table, len(rows))


def _sync_heartrate(t: Tracker, headers: dict[str, str]) -> None:
    cursor_key = "oura:heartrate"
    since, until = _resolve_datetime_window(t, cursor_key)
    records = _fetch_paginated(
        headers, "/heartrate", {"start_datetime": since, "end_datetime": until}
    )
    rows = [_flat_heartrate(r) for r in records if r.get("timestamp")]
    if rows:
        t.upsert("oura_heartrate", rows, key=["timestamp", "source"])
        Cursor(cursor_key, t.cfg.state_dir).set(max(r["timestamp"] for r in rows))
    t.log.info("oura oura_heartrate: %d", len(rows))


# --- entry points -----------------------------------------------------------


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    headers = _auth_headers(t.cfg)
    _sync_date_resource(
        t,
        headers=headers,
        path="/daily_activity",
        table="oura_daily_activity",
        cursor_key="oura:daily_activity",
        flatten=_flat_daily_activity,
    )
    _sync_date_resource(
        t,
        headers=headers,
        path="/daily_readiness",
        table="oura_daily_readiness",
        cursor_key="oura:daily_readiness",
        flatten=_flat_daily_readiness,
    )
    _sync_date_resource(
        t,
        headers=headers,
        path="/daily_sleep",
        table="oura_daily_sleep",
        cursor_key="oura:daily_sleep",
        flatten=_flat_daily_sleep,
    )
    _sync_date_resource(
        t,
        headers=headers,
        path="/daily_stress",
        table="oura_daily_stress",
        cursor_key="oura:daily_stress",
        flatten=_flat_daily_stress,
    )
    _sync_date_resource(
        t,
        headers=headers,
        path="/daily_spo2",
        table="oura_daily_spo2",
        cursor_key="oura:daily_spo2",
        flatten=_flat_daily_spo2,
    )
    _sync_date_resource(
        t,
        headers=headers,
        path="/sleep",
        table="oura_sleep",
        cursor_key="oura:sleep",
        flatten=_flat_sleep,
    )
    _sync_datetime_resource(
        t,
        headers=headers,
        path="/workout",
        table="oura_workout",
        cursor_key="oura:workout",
        flatten=_flat_workout,
        cursor_field="start_datetime",
    )
    _sync_datetime_resource(
        t,
        headers=headers,
        path="/session",
        table="oura_session",
        cursor_key="oura:session",
        flatten=_flat_session,
        cursor_field="start_datetime",
    )
    _sync_heartrate(t, headers)
