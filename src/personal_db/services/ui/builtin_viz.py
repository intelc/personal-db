"""Built-in dashboard visualizations that don't belong to any single tracker.

Use the `_builtin:` slug prefix.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape

from personal_db.core.config import Config
from personal_db.core.data_horizon import get_all as _get_all_horizons
from personal_db.core.intervals import parse_every as _parse_every
from personal_db.core.manifest import (
    ManifestError,
    ScheduleSpec,
    humanize_tracker_name,
    load_manifest,
)


def humanize_age(d: timedelta) -> str:
    s = int(d.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _format_duration(seconds: int) -> str:
    """Plain (non-"ago") duration format for daemon uptime, e.g. "2h 14m"."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def _humanize_delta(d: timedelta) -> str:
    """Forward-looking sibling of `humanize_age` (no "ago" suffix) -- used to
    render "next in ~2h" rather than "2h ago"."""
    s = int(d.total_seconds())
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def compute_next_sync(
    schedule: ScheduleSpec | None, last_run_dt: datetime | None, now: datetime
) -> str | None:
    """Human display for when a tracker's next sync will run.

    Shared by the /health rows and the /setup overview cards so both surfaces
    agree on what "next in ~2h" means.

    - `every`-schedules: last_run + parsed(every), clamped to "due now" once
      that instant is in the past (or there's no recorded last run at all --
      nothing to count forward from, so it's due immediately).
    - `cron`-schedules: launchd owns the cadence, not us -- we don't parse
      cron expressions here, so we just say "on schedule" rather than fake a
      precise countdown.
    - no schedule at all: None (nothing to show).
    """
    if schedule is None:
        return None
    if schedule.every:
        if last_run_dt is None:
            return "due now"
        remaining = (last_run_dt + _parse_every(schedule.every)) - now
        if remaining <= timedelta(0):
            return "due now"
        return f"next in ~{_humanize_delta(remaining)}"
    if schedule.cron:
        return "on schedule"
    return None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _last_errors_by_tracker(cfg: Config) -> dict[str, dict]:
    """Latest sync_errors.jsonl record per tracker, keyed by tracker name.

    Records are appended by core.sync (`_record_transform_error` and the
    per-tracker except-clause in `sync_due`/`sync_one`), one JSON object per
    line: {"ts": iso8601, "tracker": str, "error": str, "tb": str, and
    optionally "transform": str for per-transform failures}. `ts` strings are
    all `datetime.now(UTC).isoformat()`, so lexical comparison sorts them
    chronologically.
    """
    err_path = cfg.state_dir / "sync_errors.jsonl"
    latest: dict[str, dict] = {}
    if not err_path.exists():
        return latest
    try:
        with err_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tracker = rec.get("tracker")
                ts = rec.get("ts")
                if not tracker or not ts:
                    continue
                prev = latest.get(tracker)
                if prev is None or ts > prev.get("ts", ""):
                    latest[tracker] = rec
    except OSError:
        pass
    return latest


def build_health_page_data(
    cfg: Config,
    *,
    uptime_seconds: int,
    app_version: str,
    db_user_version: int,
) -> dict:
    """Data for the /health page: daemon stats + one row per installed tracker.

    A tracker's row carries an `error` entry only when the last recorded
    sync_errors.jsonl failure for it is newer than its last recorded success
    in state/last_run.json (or there's no recorded success at all) -- an old
    error followed by a later success is resolved and shouldn't show.
    """
    last_run_path = cfg.state_dir / "last_run.json"
    last_runs: dict[str, str] = {}
    if last_run_path.exists():
        try:
            last_runs = json.loads(last_run_path.read_text())
        except json.JSONDecodeError:
            last_runs = {}

    last_errors = _last_errors_by_tracker(cfg)

    installed: list[str] = []
    if cfg.trackers_dir.exists():
        installed = sorted(
            d.name
            for d in cfg.trackers_dir.iterdir()
            if d.is_dir() and (d / "manifest.yaml").exists()
        )

    now = datetime.now(timezone.utc)
    rows = []
    for tracker in installed:
        title = humanize_tracker_name(tracker)
        last_run_dt = _parse_ts(last_runs.get(tracker))
        last_sync_age = humanize_age(now - last_run_dt) if last_run_dt else None

        schedule_text = None
        try:
            manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
            title = manifest.display_title()
            if manifest.schedule and manifest.schedule.every:
                schedule_text = f"every {manifest.schedule.every}"
                next_text = compute_next_sync(manifest.schedule, last_run_dt, now)
                if next_text:
                    schedule_text = f"{schedule_text} · {next_text}"
            elif manifest.schedule and manifest.schedule.cron:
                schedule_text = compute_next_sync(manifest.schedule, last_run_dt, now)
        except ManifestError:
            pass

        error_entry = None
        rec = last_errors.get(tracker)
        if rec is not None:
            error_dt = _parse_ts(rec.get("ts"))
            if error_dt is not None and (last_run_dt is None or error_dt > last_run_dt):
                error_text = str(rec.get("error", "")).strip()
                first_line = error_text.splitlines()[0] if error_text else "(no message)"
                tb = str(rec.get("tb", "")).strip()
                full_text = f"{error_text}\n\n{tb}" if tb else error_text
                error_entry = {
                    "age": humanize_age(now - error_dt),
                    "first_line": first_line,
                    "full_text": full_text,
                }

        rows.append(
            {
                "slug": tracker,
                "title": title,
                "last_sync_age": last_sync_age,
                "schedule": schedule_text,
                "error": error_entry,
            }
        )

    return {
        "uptime": _format_duration(uptime_seconds),
        "app_version": app_version,
        "db_user_version": db_user_version,
        "rows": rows,
    }


def render_health(cfg: Config) -> str:
    # Trailing link to the full /health page (sync-error surfacing,
    # schedules) -- this table is just last-sync-age + data horizon, so
    # anyone landing here from "this looks stale" needs a way to see why.
    footer_link = '<p class="meta"><a href="/health">Health &rarr;</a></p>'
    last_run_path = cfg.state_dir / "last_run.json"
    last_runs: dict[str, str] = {}
    if last_run_path.exists():
        try:
            last_runs = json.loads(last_run_path.read_text())
        except json.JSONDecodeError:
            last_runs = {}
    horizons = _get_all_horizons(cfg)
    if not last_runs:
        return '<p class="meta">no syncs recorded yet</p>' + footer_link
    now = datetime.now(timezone.utc)
    rows = []
    for tracker, ts in sorted(last_runs.items()):
        try:
            age = humanize_age(now - datetime.fromisoformat(ts))
        except ValueError:
            age = "?"
        horizon = horizons.get(tracker)
        horizon_cell = escape(horizon[:10]) if horizon else "—"
        # Tracker name links to its dedicated page so users can click straight
        # from "this looks stale" to the tracker's recent rows / viz.
        name_link = (
            f'<a href="/t/{escape(tracker)}" title="{escape(tracker)}">'
            f"{escape(humanize_tracker_name(tracker))}</a>"
        )
        rows.append(
            f"<tr><td>{name_link}</td>"
            f"<td>{escape(age)}</td>"
            f'<td class="meta">{horizon_cell}</td></tr>'
        )
    return (
        '<table class="health">'
        "<thead><tr><th>tracker</th><th>last sync</th><th>data horizon</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    ) + footer_link


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "health",
            "name": "Tracker Health",
            "description": "Last sync age and recorded data horizon for every installed tracker.",
            "render": render_health,
        },
    ]
