import os
from datetime import UTC, datetime

import requests

from personal_db.tracker import Tracker

API = "https://api.github.com"


def _flatten_item(item: dict) -> dict:
    msg = (item.get("commit", {}).get("message") or "").splitlines()[0][:500]
    raw_date = item["commit"]["author"]["date"]
    # Normalize to UTC ISO-8601 (handle "-04:00" style offsets)
    committed_at = (
        datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    )
    return {
        "sha": item["sha"],
        "repo": item["repository"]["full_name"],
        "committed_at": committed_at,
        "message": msg,
        "additions": None,
        "deletions": None,
    }


def _fetch(url: str, headers: dict) -> tuple[list[dict], str | None]:
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    next_link = None
    link_hdr = r.headers.get("Link", "")
    for part in link_hdr.split(","):
        if 'rel="next"' in part:
            next_link = part.split(";")[0].strip().lstrip("<").rstrip(">")
    body = r.json()
    return body.get("items", []), next_link


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    user = os.environ.get("GITHUB_USER")
    if not token or not user:
        raise RuntimeError("Set GITHUB_TOKEN and GITHUB_USER env vars (see manifest setup_steps)")
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.cloak-preview",
    }
    cursor = t.cursor.get()
    url = f"{API}/search/commits?q=author:{user}&sort=author-date&order=desc&per_page=100"
    all_rows: list[dict] = []
    while url:
        items, url = _fetch(url, headers)
        for it in items:
            row = _flatten_item(it)
            if cursor and row["committed_at"] <= cursor:
                url = None
                break
            all_rows.append(row)
    if all_rows:
        t.upsert("github_commits", all_rows, key=["sha"])
        t.cursor.set(max(r["committed_at"] for r in all_rows))
    t.log.info("github_commits: ingested %d commits", len(all_rows))
