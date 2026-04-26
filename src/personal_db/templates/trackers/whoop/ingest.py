import os

import requests

from personal_db.oauth import refresh_if_needed
from personal_db.tracker import Tracker

WHOOP_API = "https://api.prod.whoop.com/developer"


def _client_credentials():
    cid = os.environ.get("WHOOP_CLIENT_ID")
    cs = os.environ.get("WHOOP_CLIENT_SECRET")
    if not cid or not cs:
        raise RuntimeError("Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET")
    return cid, cs


def _flatten(rec: dict) -> dict:
    score = rec.get("score") or {}
    return {
        "id": str(rec["id"]),
        "start": rec["start"],
        "end": rec.get("end"),
        "strain": score.get("strain"),
        "average_heart_rate": score.get("average_heart_rate"),
    }


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    cid, cs = _client_credentials()
    token = refresh_if_needed(
        t.cfg,
        "whoop",
        token_url=f"{WHOOP_API}/oauth/oauth2/token",
        client_id=cid,
        client_secret=cs,
    )
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    cursor = t.cursor.get()
    params = {"limit": 25}
    if cursor:
        params["start"] = cursor
    rows: list[dict] = []
    next_token = None
    while True:
        if next_token:
            params["nextToken"] = next_token
        r = requests.get(f"{WHOOP_API}/v1/cycle", headers=headers, params=params, timeout=15)
        r.raise_for_status()
        body = r.json()
        rows.extend(_flatten(rec) for rec in body.get("records", []))
        next_token = body.get("next_token")
        if not next_token:
            break
    if rows:
        t.upsert("whoop_cycles", rows, key=["id"])
        t.cursor.set(max(r["start"] for r in rows))
    t.log.info("whoop: ingested %d cycles", len(rows))
