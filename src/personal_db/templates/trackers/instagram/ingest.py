"""Instagram Reels ingest.

Discovers new reels and writes a per-reel insights snapshot whenever
that reel is "due" under the tiered sampling cadence (see TIERS):
hourly in the first 2 days, every 3h through day 7, daily through day
180, then it stops. Reel metadata goes into `reels_media` (immutable
per reel); insights go into `reels_insights_snapshots` keyed by
(media_id, snapshot_at) so growth curves over time are preserved.

The daemon fires sync hourly. Each run only snapshots reels whose last
snapshot is older than their tier's interval — so old reels don't get
re-hit 24x/day just because the daemon wakes up that often.

Quota note: ~200 calls/hr/user. At steady state with 1 reel/day, peak
load is ~12 insights calls/hour — comfortable.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from personal_db.oauth import refresh_if_needed
from personal_db.tracker import Tracker

GRAPH = "https://graph.instagram.com"

ACCOUNT_PROFILE_FIELDS = [
    "id",
    "username",
    "name",
    "account_type",
    "media_count",
    "followers_count",
    "follows_count",
]
ACCOUNT_PROFILE_FALLBACK_FIELDS = [
    "id",
    "username",
    "account_type",
    "media_count",
]

# Metrics IG accepts on a vanilla creator/business account without
# Facebook crossposting integration. The fuller set (incl. `reposts`,
# `crossposted_views`, `facebook_views`) is advertised in the docs but
# returns 400 / "Fatal" on accounts that aren't FB-linked or below
# whatever access tier IG gates `reposts` behind.
REELS_METRICS = [
    "views",
    "reach",
    "likes",
    "comments",
    "shares",
    "saved",
    "total_interactions",
    "ig_reels_avg_watch_time",
    "ig_reels_video_view_total_time",
    "reels_skip_rate",
]
# Tiered sampling: (max_age_days, snapshot_interval_seconds). Reels older
# than the last band's max_age stop accruing snapshots — by then the view
# curve is flat. Crank the last entry if you find late-tail growth on
# your account.
TIERS: list[tuple[int, int]] = [
    (2,   1 * 3600),   # day 0-2: hourly
    (7,   3 * 3600),   # day 2-7: every 3 hours
    (180, 24 * 3600),  # day 7-180: daily
]
ACTIVE_WINDOW_DAYS = TIERS[-1][0]
# Sync runs aren't perfectly periodic — if the scheduler fires at 12:00:30
# and the next at 13:00:25, the gap is just under 1h and would miss the
# hourly tier without this tolerance.
DUE_TOLERANCE_S = 300


def _credentials() -> tuple[str, str]:
    cid = os.environ.get("INSTAGRAM_APP_ID")
    cs = os.environ.get("INSTAGRAM_APP_SECRET")
    if not cid or not cs:
        raise RuntimeError("Set INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET")
    return cid, cs


def _fetch_ig_user_id(access_token: str) -> str:
    return _fetch_account_profile(access_token)["id"]


def _fetch_account_profile(access_token: str) -> dict[str, Any]:
    """Fetch current account-level profile counts.

    Instagram's profile field surface varies by API mode and account setup.
    We try the count-rich field set first, then fall back to basic profile
    fields so media ingest can continue even if follower counts are gated.
    """
    for fields in (ACCOUNT_PROFILE_FIELDS, ACCOUNT_PROFILE_FALLBACK_FIELDS):
        r = requests.get(
            f"{GRAPH}/me",
            params={"fields": ",".join(fields), "access_token": access_token},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        if fields is ACCOUNT_PROFILE_FALLBACK_FIELDS:
            r.raise_for_status()
    raise RuntimeError("unreachable")


def _walk_media(
    access_token: str,
    ig_user_id: str,
    *,
    stop_at_media_id: str | None,
) -> list[dict[str, Any]]:
    """Walk /me/media pages newest-first until we hit `stop_at_media_id`
    (exclusive) or run out of pages. None = full history."""
    url = f"{GRAPH}/{ig_user_id}/media"
    params: dict[str, Any] | None = {
        "fields": "id,media_product_type,media_type,caption,permalink,thumbnail_url,timestamp",
        "limit": 100,
        "access_token": access_token,
    }
    out: list[dict[str, Any]] = []
    while url:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        body = r.json()
        for item in body.get("data") or []:
            if stop_at_media_id and item["id"] == stop_at_media_id:
                return out
            out.append(item)
        url = (body.get("paging") or {}).get("next")
        params = None  # paging.next already has all params baked in
    return out


def _fetch_insights(
    access_token: str, media_id: str, log: Any
) -> dict[str, Any] | None:
    """Fetch insights for one media. Returns flat metric_name → value, or
    None if IG rejects the request (e.g. media older than retention or
    a metric isn't supported for this account)."""
    r = requests.get(
        f"{GRAPH}/{media_id}/insights",
        params={"metric": ",".join(REELS_METRICS), "access_token": access_token},
        timeout=15,
    )
    if r.status_code != 200:
        msg = (r.json().get("error") or {}).get("message", r.text[:200])
        log.warning("instagram: insights %s failed: %s", media_id, msg)
        return None
    body = r.json()
    out: dict[str, Any] = {}
    for entry in body.get("data") or []:
        values = entry.get("values") or []
        out[entry["name"]] = values[0]["value"] if values else None
    return out


def _parse_iso(s: str) -> datetime:
    """IG returns `+0000`; our snapshots use `+00:00`. fromisoformat in
    Python 3.11+ accepts both, but normalize for safety."""
    if s.endswith("+0000"):
        s = s[:-5] + "+00:00"
    return datetime.fromisoformat(s)


def _cadence_seconds(age_days: float) -> int | None:
    """Return the snapshot interval for a reel of `age_days`, or None if
    it has aged out of the last tier and should no longer be snapshotted."""
    for max_age, interval in TIERS:
        if age_days < max_age:
            return interval
    return None


def _due_reel_ids(db_path: Path, now: datetime) -> list[str]:
    """Reels whose last snapshot is older than their tier's interval."""
    cutoff_iso = (now - timedelta(days=ACTIVE_WINDOW_DAYS)).isoformat()
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT m.media_id, m.timestamp,
                   (SELECT MAX(snapshot_at)
                    FROM reels_insights_snapshots
                    WHERE media_id = m.media_id) AS last_snap
            FROM reels_media m
            WHERE m.media_product_type = 'REELS' AND m.timestamp >= ?
            ORDER BY m.timestamp DESC
            """,
            (cutoff_iso,),
        ).fetchall()
    finally:
        con.close()

    due: list[str] = []
    for media_id, posted_iso, last_snap_iso in rows:
        age_days = (now - _parse_iso(posted_iso)).total_seconds() / 86400
        interval = _cadence_seconds(age_days)
        if interval is None:
            continue
        if last_snap_iso is None:
            due.append(media_id)
            continue
        elapsed = (now - _parse_iso(last_snap_iso)).total_seconds()
        if elapsed >= interval - DUE_TOLERANCE_S:
            due.append(media_id)
    return due


def sync(t: Tracker) -> None:
    cid, cs = _credentials()
    token = refresh_if_needed(
        t.cfg,
        "instagram",
        token_url="",  # adapter ignores
        client_id=cid,
        client_secret=cs,
    )
    access_token = token["access_token"]
    account_profile = _fetch_account_profile(access_token)
    ig_user_id = token.get("user_id") or account_profile["id"]

    latest_seen = t.cursor.get() or None
    new_media = _walk_media(access_token, ig_user_id, stop_at_media_id=latest_seen)

    now = datetime.now(UTC)
    now_iso = now.isoformat()

    media_rows = [
        {
            "media_id": item["id"],
            "ig_user_id": ig_user_id,
            "media_product_type": item.get("media_product_type"),
            "media_type": item.get("media_type"),
            "permalink": item.get("permalink"),
            "caption": item.get("caption"),
            "thumbnail_url": item.get("thumbnail_url"),
            "timestamp": item.get("timestamp"),
            "fetched_at": now_iso,
        }
        for item in new_media
    ]
    if media_rows:
        t.upsert("reels_media", media_rows, key=["media_id"])
        t.cursor.set(new_media[0]["id"])

    t.upsert(
        "instagram_account_snapshots",
        [
            {
                "ig_user_id": ig_user_id,
                "snapshot_at": now_iso,
                "username": account_profile.get("username"),
                "name": account_profile.get("name"),
                "account_type": account_profile.get("account_type"),
                "media_count": account_profile.get("media_count"),
                "followers_count": account_profile.get("followers_count")
                or account_profile.get("follower_count"),
                "follows_count": account_profile.get("follows_count"),
                "raw_json": json.dumps(account_profile, sort_keys=True),
            }
        ],
        key=["ig_user_id", "snapshot_at"],
    )

    due_reels = _due_reel_ids(t.cfg.db_path, now)

    snapshot_rows: list[dict[str, Any]] = []
    for media_id in due_reels:
        metrics = _fetch_insights(access_token, media_id, t.log)
        if metrics is None:
            continue
        snapshot_rows.append(
            {
                "media_id": media_id,
                "snapshot_at": now_iso,
                "views": metrics.get("views"),
                "reach": metrics.get("reach"),
                "likes": metrics.get("likes"),
                "comments": metrics.get("comments"),
                "shares": metrics.get("shares"),
                "saved": metrics.get("saved"),
                "reposts": None,
                "total_interactions": metrics.get("total_interactions"),
                "ig_reels_avg_watch_time_ms": metrics.get("ig_reels_avg_watch_time"),
                "ig_reels_video_view_total_time_ms": metrics.get(
                    "ig_reels_video_view_total_time"
                ),
                "reels_skip_rate_pct": metrics.get("reels_skip_rate"),
                "crossposted_views": None,
                "facebook_views": None,
            }
        )

    if snapshot_rows:
        t.upsert(
            "reels_insights_snapshots",
            snapshot_rows,
            key=["media_id", "snapshot_at"],
        )

    t.log.info(
        "instagram: %d new media, %d insight snapshots",
        len(media_rows),
        len(snapshot_rows),
    )


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    """Reset cursor and re-walk /me/media from the top. The (start, end) args
    are accepted for interface compatibility but ignored — IG's media listing
    has no time-range filter; we always walk newest-first."""
    t.cursor.set("")
    sync(t)
