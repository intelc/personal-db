"""project_time — derived tracker.

Aggregates hours per (date, project) from:
  - claude_sessions   (start..last_msg, capped per session)
  - codex_sessions    (start..last_event, capped per session)
  - screen_time_app_usage (matched by bundle_id)
  - github_commits    (commit count only — not attributed as hours)

Date attribution: a session/event is attributed to the LOCAL date of its start.
This is intentional: after capping at cap_session_hours, midnight-crossing is rare.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml


# ---------- helpers ----------

def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _parse_iso(s: str) -> datetime | None:
    """Parse an ISO-8601 timestamp. Returns aware datetime in UTC, or None."""
    if not s:
        return None
    s = s.strip()
    # SQLite often stores 'Z' suffix; Python <3.11 doesn't accept it directly.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _local_date(dt: datetime, tz) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d")


def _load_config(t) -> dict:
    cfg_path = Path(t.cfg.trackers_dir) / "project_time" / "projects.yaml"
    if not cfg_path.exists():
        return {"cap_session_hours": 4.0, "projects": []}
    with cfg_path.open() as f:
        return yaml.safe_load(f) or {}


def _build_indexes(projects: list[dict]) -> tuple[dict, list, dict]:
    """Build O(1) lookup indexes from the projects list.

    Returns:
      repo_to_proj:    repo full_name -> project name
      cwd_prefixes:    list of (cwd_prefix, project name)  (longest-prefix matching)
      bundle_to_proj:  bundle_id -> project name
    """
    repo_to_proj: dict[str, str] = {}
    cwd_prefixes: list[tuple[str, str]] = []
    bundle_to_proj: dict[str, str] = {}

    for p in projects:
        name = p["name"]
        for r in p.get("repos") or []:
            repo_to_proj[r] = name
        for cwd in p.get("cwds") or []:
            cwd_prefixes.append((cwd.rstrip("/"), name))
        for b in p.get("bundle_ids") or []:
            bundle_to_proj[b] = name

    # longest prefix first so e.g. /a/b/c wins over /a/b
    cwd_prefixes.sort(key=lambda x: -len(x[0]))
    return repo_to_proj, cwd_prefixes, bundle_to_proj


def _match_cwd(path: str, cwd_prefixes: list[tuple[str, str]]) -> str | None:
    if not path:
        return None
    p = path.rstrip("/")
    for prefix, proj in cwd_prefixes:
        if p == prefix or p.startswith(prefix + "/"):
            return proj
    return None


def _slug_to_cwd(slug: str) -> str:
    """Claude project_slug encoding: '/' -> '-'. Reverse it heuristically."""
    if not slug:
        return ""
    # leading '-' becomes '/'
    if slug.startswith("-"):
        return "/" + slug[1:].replace("-", "/")
    return slug.replace("-", "/")


# ---------- per-source extractors ----------

def _collect_claude(con, start_dt, end_dt, cwd_prefixes, cap_h, tz, agg):
    if not _table_exists(con, "claude_sessions"):
        return
    rows = con.execute(
        """
        SELECT project_slug, started_at, last_msg_at
        FROM claude_sessions
        WHERE started_at >= ? AND started_at < ?
        """,
        (start_dt.isoformat(), end_dt.isoformat()),
    ).fetchall()
    for slug, started, last_msg in rows:
        s = _parse_iso(started)
        e = _parse_iso(last_msg) or s
        if s is None:
            continue
        # try direct slug match (in case slug already matches a registered cwd via reverse)
        # but the canonical matcher is reverse-encode the slug back into a path.
        path_guess = _slug_to_cwd(slug)
        proj = _match_cwd(path_guess, cwd_prefixes)
        if not proj:
            continue
        hours = max(0.0, (e - s).total_seconds() / 3600.0)
        hours = min(hours, cap_h)
        if hours <= 0:
            continue
        date = _local_date(s, tz)
        agg[(date, proj)]["claude"] += hours


def _collect_codex(con, start_dt, end_dt, cwd_prefixes, cap_h, tz, agg):
    if not _table_exists(con, "codex_sessions"):
        return
    rows = con.execute(
        """
        SELECT cwd, started_at, last_event_at
        FROM codex_sessions
        WHERE started_at >= ? AND started_at < ?
        """,
        (start_dt.isoformat(), end_dt.isoformat()),
    ).fetchall()
    for cwd, started, last_event in rows:
        s = _parse_iso(started)
        e = _parse_iso(last_event) or s
        if s is None:
            continue
        proj = _match_cwd(cwd or "", cwd_prefixes)
        if not proj:
            continue
        hours = max(0.0, (e - s).total_seconds() / 3600.0)
        hours = min(hours, cap_h)
        if hours <= 0:
            continue
        date = _local_date(s, tz)
        agg[(date, proj)]["codex"] += hours


def _collect_app(con, start_dt, end_dt, bundle_to_proj, tz, agg):
    if not _table_exists(con, "screen_time_app_usage") or not bundle_to_proj:
        return
    placeholders = ",".join(["?"] * len(bundle_to_proj))
    params = [start_dt.isoformat(), end_dt.isoformat(), *bundle_to_proj.keys()]
    rows = con.execute(
        f"""
        SELECT bundle_id, start_at, seconds
        FROM screen_time_app_usage
        WHERE start_at >= ? AND start_at < ?
          AND bundle_id IN ({placeholders})
        """,
        params,
    ).fetchall()
    for bundle, start_at, seconds in rows:
        s = _parse_iso(start_at)
        if s is None or not seconds:
            continue
        proj = bundle_to_proj.get(bundle)
        if not proj:
            continue
        hours = float(seconds) / 3600.0
        if hours <= 0:
            continue
        date = _local_date(s, tz)
        agg[(date, proj)]["app"] += hours


def _collect_commits(con, start_dt, end_dt, repo_to_proj, tz, commit_agg):
    if not _table_exists(con, "github_commits") or not repo_to_proj:
        return
    placeholders = ",".join(["?"] * len(repo_to_proj))
    params = [start_dt.isoformat(), end_dt.isoformat(), *repo_to_proj.keys()]
    rows = con.execute(
        f"""
        SELECT repo, committed_at
        FROM github_commits
        WHERE committed_at >= ? AND committed_at < ?
          AND repo IN ({placeholders})
        """,
        params,
    ).fetchall()
    for repo, committed_at in rows:
        s = _parse_iso(committed_at)
        if s is None:
            continue
        proj = repo_to_proj.get(repo)
        if not proj:
            continue
        date = _local_date(s, tz)
        commit_agg[(date, proj)] += 1


# ---------- core compute ----------

def _compute(t, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Compute project_time rows for [start_dt, end_dt). Both UTC-aware datetimes."""
    cfg = _load_config(t)
    cap_h = float(cfg.get("cap_session_hours", 4.0))
    projects = cfg.get("projects") or []
    repo_to_proj, cwd_prefixes, bundle_to_proj = _build_indexes(projects)
    tz = _local_tz()

    # (date, project) -> {"claude": h, "codex": h, "app": h}
    agg: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"claude": 0.0, "codex": 0.0, "app": 0.0}
    )
    commit_agg: dict[tuple[str, str], int] = defaultdict(int)

    con = sqlite3.connect(t.cfg.db_path)
    try:
        _collect_claude(con, start_dt, end_dt, cwd_prefixes, cap_h, tz, agg)
        _collect_codex(con, start_dt, end_dt, cwd_prefixes, cap_h, tz, agg)
        _collect_app(con, start_dt, end_dt, bundle_to_proj, tz, agg)
        _collect_commits(con, start_dt, end_dt, repo_to_proj, tz, commit_agg)
    finally:
        con.close()

    keys = set(agg.keys()) | set(commit_agg.keys())
    rows: list[dict] = []
    for key in sorted(keys):
        date, project = key
        breakdown = agg.get(key, {"claude": 0.0, "codex": 0.0, "app": 0.0})
        breakdown = {k: round(v, 4) for k, v in breakdown.items()}
        total = round(sum(breakdown.values()), 4)
        rows.append({
            "date": date,
            "project": project,
            "hours": total,
            "commit_count": commit_agg.get(key, 0),
            "breakdown_json": json.dumps(breakdown, separators=(",", ":")),
        })
    return rows


def _delete_window(t, start_local_date: str, end_local_date: str) -> None:
    """Delete existing rows in [start_local_date, end_local_date] before re-upsert."""
    con = sqlite3.connect(t.cfg.db_path)
    try:
        con.execute(
            "DELETE FROM project_time WHERE date >= ? AND date <= ?",
            (start_local_date, end_local_date),
        )
        con.commit()
    finally:
        con.close()


# ---------- public entry points ----------

def sync(t) -> None:
    """Recompute the last 2 days plus everything since cursor.

    On a fresh install (no cursor), starts from today - 90 days.
    """
    tz = _local_tz()
    today_local = datetime.now(tz).date()

    cursor = t.cursor.get()  # last-processed local date as 'YYYY-MM-DD' or None
    if cursor:
        try:
            cursor_date = datetime.strptime(cursor, "%Y-%m-%d").date()
        except ValueError:
            cursor_date = today_local - timedelta(days=90)
    else:
        cursor_date = today_local - timedelta(days=90)

    # Always recompute trailing 2 days to catch late-arriving data.
    start_local = min(cursor_date, today_local - timedelta(days=2))
    end_local = today_local  # inclusive of today

    # Convert local-date window to UTC datetime range that covers any local date in [start, end]
    start_dt = datetime.combine(start_local, datetime.min.time(), tz).astimezone(timezone.utc)
    end_dt = datetime.combine(
        end_local + timedelta(days=1), datetime.min.time(), tz
    ).astimezone(timezone.utc)

    rows = _compute(t, start_dt, end_dt)

    # Wipe the recomputed window so projects that newly drop to 0 hours disappear,
    # and so projects.yaml edits are reflected.
    _delete_window(t, start_local.isoformat(), end_local.isoformat())

    if rows:
        t.upsert("project_time", rows, key=["date", "project"])

    t.cursor.set(today_local.isoformat())


def backfill(t, start, end) -> None:
    """Recompute [start, end] (inclusive). start/end can be date or datetime."""
    tz = _local_tz()

    def _to_date(x):
        if isinstance(x, datetime):
            return x.astimezone(tz).date()
        if hasattr(x, "year") and hasattr(x, "month") and hasattr(x, "day"):
            return x  # date
        return datetime.fromisoformat(str(x)).date()

    start_local = _to_date(start)
    end_local = _to_date(end)
    if end_local < start_local:
        start_local, end_local = end_local, start_local

    start_dt = datetime.combine(start_local, datetime.min.time(), tz).astimezone(timezone.utc)
    end_dt = datetime.combine(
        end_local + timedelta(days=1), datetime.min.time(), tz
    ).astimezone(timezone.utc)

    rows = _compute(t, start_dt, end_dt)
    _delete_window(t, start_local.isoformat(), end_local.isoformat())
    if rows:
        t.upsert("project_time", rows, key=["date", "project"])

    today_local = datetime.now(tz).date()
    if end_local >= today_local:
        t.cursor.set(today_local.isoformat())
