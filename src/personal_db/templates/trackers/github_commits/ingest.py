import os

import requests

from personal_db.tracker import Tracker

API = "https://api.github.com"


def _flatten_event(ev: dict) -> list[dict]:
    rows = []
    if ev.get("type") != "PushEvent":
        return rows
    repo = ev.get("repo", {}).get("name", "")
    for c in ev.get("payload", {}).get("commits", []):
        rows.append(
            {
                "sha": c["sha"],
                "repo": repo,
                "committed_at": ev["created_at"],  # event time as a proxy
                "message": (c.get("message") or "").splitlines()[0][:500],
                "additions": None,
                "deletions": None,
            }
        )
    return rows


def _fetch(url: str, headers: dict) -> tuple[list[dict], str | None]:
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    next_link = None
    link_hdr = r.headers.get("Link", "")
    for part in link_hdr.split(","):
        if 'rel="next"' in part:
            next_link = part.split(";")[0].strip().lstrip("<").rstrip(">")
    return r.json(), next_link


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)  # Events API only returns ~90 days; backfill == sync for v0


def sync(t: Tracker) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    user = os.environ.get("GITHUB_USER")
    if not token or not user:
        raise RuntimeError("Set GITHUB_TOKEN and GITHUB_USER env vars (see manifest setup_steps)")
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    url = f"{API}/users/{user}/events/public?per_page=100"
    cursor = t.cursor.get()
    all_rows: list[dict] = []
    while url:
        events, url = _fetch(url, headers)
        for ev in events:
            if cursor and ev["created_at"] <= cursor:
                url = None  # stop paginating; we've reached the cursor
                break
            all_rows.extend(_flatten_event(ev))
    if all_rows:
        t.upsert("github_commits", all_rows, key=["sha"])
        t.cursor.set(max(r["committed_at"] for r in all_rows))
    t.log.info("github_commits: ingested %d rows", len(all_rows))
