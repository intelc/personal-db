import json
import os
from datetime import UTC, datetime, timedelta

import requests

from personal_db.tracker import Tracker

API = "https://api.omi.me/v1/dev/user/conversations"
PAGE_SIZE = 50
BACKFILL_DAYS = 90


def _headers() -> dict:
    key = os.environ.get("OMI_API_KEY")
    if not key:
        raise RuntimeError("Set OMI_API_KEY env var (see manifest setup_steps)")
    return {"Authorization": f"Bearer {key}"}


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_utc_iso(s: str | None) -> str | None:
    dt = _parse_iso(s)
    return dt.astimezone(UTC).isoformat() if dt else None


def _flatten(conv: dict) -> dict | None:
    cid = conv.get("id")
    started = conv.get("started_at") or conv.get("created_at")
    started_iso = _to_utc_iso(started)
    if not cid or not started_iso:
        return None

    finished_iso = _to_utc_iso(conv.get("finished_at"))
    duration = None
    s_dt = _parse_iso(started)
    f_dt = _parse_iso(conv.get("finished_at"))
    if s_dt and f_dt:
        duration = int((f_dt - s_dt).total_seconds())

    # Omi nests title/overview/category/action_items under a "structured" object.
    structured = conv.get("structured") or {}

    segments = conv.get("transcript_segments") or []
    transcript_lines = []
    for seg in segments:
        if seg.get("is_user"):
            speaker = "User"
        else:
            speaker = seg.get("speaker_name") or seg.get("speaker") or "Other"
        text = (seg.get("text") or "").strip()
        if text:
            transcript_lines.append(f"{speaker}: {text}")
    transcript = "\n".join(transcript_lines)

    action_items = json.dumps(structured.get("action_items") or [])

    return {
        "id": cid,
        "started_at": started_iso,
        "finished_at": finished_iso,
        "duration_seconds": duration,
        "title": (structured.get("title") or "")[:500],
        "overview": structured.get("overview") or "",
        "transcript": transcript,
        "action_items": action_items,
        "category": structured.get("category") or "",
        "source": conv.get("source") or "",
    }


def _fetch_since(since: datetime, headers: dict) -> list[dict]:
    """Page back through Omi conversations until we hit ``since``.

    The API returns most-recent first; we stop the moment a page yields a
    conversation older than the cursor."""
    end = datetime.now(UTC) + timedelta(days=1)
    out: list[dict] = []
    offset = 0
    while True:
        params = {
            "limit": PAGE_SIZE,
            "offset": offset,
            "include_transcript": "true",
            "start_date": since.date().isoformat(),
            "end_date": end.date().isoformat(),
        }
        r = requests.get(API, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        page = r.json()
        if not isinstance(page, list) or not page:
            break
        for conv in page:
            row = _flatten(conv)
            if not row:
                continue
            if datetime.fromisoformat(row["started_at"]) < since:
                # API can return out-of-window items; stop once we drop below the cursor.
                return out
            out.append(row)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return out


def sync(t: Tracker) -> None:
    headers = _headers()
    cursor = t.cursor.get()
    since = (
        datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        if cursor
        else datetime.now(UTC) - timedelta(days=BACKFILL_DAYS)
    )
    rows = _fetch_since(since, headers)
    if rows:
        t.upsert("omi_conversations", rows, key=["id"])
        t.cursor.set(max(r["started_at"] for r in rows))
    t.log.info("omi: ingested %d conversations since %s", len(rows), since.isoformat())


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    """Backfill historical conversations.

    ``start`` (ISO date or datetime) overrides the default 90-day window.
    ``end`` is accepted for interface compatibility but ignored — Omi returns
    everything up to "now" in a single sweep."""
    del end  # Omi has no upper-bound semantics worth honoring.
    headers = _headers()
    if start:
        since = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
    else:
        since = datetime.now(UTC) - timedelta(days=BACKFILL_DAYS)
    rows = _fetch_since(since, headers)
    if rows:
        t.upsert("omi_conversations", rows, key=["id"])
        t.cursor.set(max(r["started_at"] for r in rows))
    t.log.info("omi: backfilled %d conversations since %s", len(rows), since.isoformat())
