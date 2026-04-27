import os
from datetime import UTC, datetime, timedelta

import requests

from personal_db.tracker import Tracker

API = "https://api.github.com"


def _accepted_emails() -> frozenset[str]:
    """Comma-separated GITHUB_AUTHOR_EMAILS, lowercased + stripped."""
    raw = os.environ.get("GITHUB_AUTHOR_EMAILS", "")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def _is_user_commit(c: dict, github_user: str, emails: frozenset[str]) -> bool:
    top = c.get("author") or {}
    if top.get("login") == github_user:
        return True
    nested = (c.get("commit") or {}).get("author") or {}
    email = (nested.get("email") or "").lower()
    return email in emails


def _flatten_commit(c: dict, repo: str) -> dict:
    raw_date = c["commit"]["author"]["date"]
    committed_at = (
        datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    )
    msg = (c["commit"]["message"] or "").splitlines()[0][:500]
    return {
        "sha": c["sha"],
        "repo": repo,
        "committed_at": committed_at,
        "message": msg,
        "additions": None,
        "deletions": None,
    }


def _fetch_page(url: str, headers: dict) -> tuple[list, str | None]:
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 409:
        # Empty repo — GitHub returns 409 for /commits on a repo with no default branch
        return [], None
    r.raise_for_status()
    body = r.json()
    next_url = None
    link = r.headers.get("Link", "")
    for part in link.split(","):
        if 'rel="next"' in part:
            next_url = part.split(";")[0].strip().lstrip("<").rstrip(">")
    return body, next_url


def _fetch_repo_commits(full_name: str, since_iso: str, headers: dict) -> list[dict]:
    url = f"{API}/repos/{full_name}/commits?since={since_iso}&per_page=100"
    out = []
    while url:
        items, url = _fetch_page(url, headers)
        out.extend(items)
    return out


def _days_ago_iso(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _authed_login(headers: dict) -> str:
    r = requests.get(f"{API}/user", headers=headers, timeout=15)
    r.raise_for_status()
    login = r.json().get("login")
    if not login:
        raise RuntimeError("could not determine GitHub login from /user response")
    return login


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)


def sync(t: Tracker) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN env var (see manifest setup_steps)")
    emails = _accepted_emails()
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    user = _authed_login(headers)
    cursor = t.cursor.get()
    # First sync (no cursor): default to last 365 days. Subsequent: use stored cursor.
    since = cursor or _days_ago_iso(365)
    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))

    all_rows: list[dict] = []
    total_seen = 0
    repos_url: str | None = (
        f"{API}/user/repos?sort=pushed&direction=desc"
        f"&affiliation=owner,collaborator,organization_member&per_page=100"
    )
    while repos_url:
        repos, repos_url = _fetch_page(repos_url, headers)
        for repo in repos:
            pushed = datetime.fromisoformat(
                (repo.get("pushed_at") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")
            )
            if pushed <= since_dt:
                # Repos are sorted by pushed_at desc, so once we hit one
                # older than `since`, all subsequent are also older. Bail out.
                repos_url = None
                break
            full_name = repo["full_name"]
            commits = _fetch_repo_commits(full_name, since, headers)
            total_seen += len(commits)
            for c in commits:
                if _is_user_commit(c, user, emails):
                    all_rows.append(_flatten_commit(c, full_name))

    if all_rows:
        t.upsert("github_commits", all_rows, key=["sha"])
        t.cursor.set(max(r["committed_at"] for r in all_rows))
    t.log.info(
        "github_commits: ingested %d / %d seen across %d repos",
        len(all_rows),
        total_seen,
        len({r["repo"] for r in all_rows}),
    )
    if total_seen > 0 and not all_rows and not emails:
        t.log.info(
            "hint: 0 commits matched. Set GITHUB_AUTHOR_EMAILS in <root>/.env "
            "(comma-separated emails you commit with — find via 'git config user.email')"
        )
