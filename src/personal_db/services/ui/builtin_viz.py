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
from personal_db.core.sync_backoff import paused_trackers as _paused_trackers


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


def repeated_failure_trackers(
    cfg: Config, *, min_count: int = 2, window_hours: int = 24
) -> list[str]:
    """Trackers with repeated, still-unresolved sync failures.

    A tracker qualifies when it has at least `min_count` sync_errors.jsonl
    records within the trailing `window_hours` AND its latest error is newer
    than its last recorded success in state/last_run.json (or there's no
    recorded success at all) -- mirrors the "still failing" check in
    `build_health_page_data` above, but counts occurrences in a window
    instead of just looking at the single latest record, so one-off blips
    don't trip it.

    Powers the daemon health payload's `repeated_sync_failures` field, which
    the shell tray polls to badge itself when syncs are failing.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)

    err_path = cfg.state_dir / "sync_errors.jsonl"
    counts: dict[str, int] = {}
    latest_error_ts: dict[str, datetime] = {}
    if err_path.exists():
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
                    ts = _parse_ts(rec.get("ts"))
                    if not tracker or ts is None or ts < window_start:
                        continue
                    counts[tracker] = counts.get(tracker, 0) + 1
                    if tracker not in latest_error_ts or ts > latest_error_ts[tracker]:
                        latest_error_ts[tracker] = ts
        except OSError:
            pass

    last_run_path = cfg.state_dir / "last_run.json"
    last_runs: dict[str, str] = {}
    if last_run_path.exists():
        try:
            last_runs = json.loads(last_run_path.read_text())
        except json.JSONDecodeError:
            last_runs = {}

    result = set()
    for tracker, count in counts.items():
        if count < min_count:
            continue
        last_run_dt = _parse_ts(last_runs.get(tracker))
        error_dt = latest_error_ts[tracker]
        if last_run_dt is None or error_dt > last_run_dt:
            result.add(tracker)

    # A paused tracker (see core.sync_backoff) stops generating new
    # sync_errors.jsonl records -- sync_due no longer attempts it -- so its
    # last real failure eventually ages out of `window_hours` above even
    # though it's still failing from the user's perspective. Union in every
    # currently-paused tracker unconditionally so it doesn't silently drop
    # out of this list just because the scheduler stopped retrying it.
    result.update(_paused_trackers(cfg))
    return sorted(result)


def _installed_trackers(cfg: Config) -> list[str]:
    if not cfg.trackers_dir.exists():
        return []
    return sorted(
        d.name
        for d in cfg.trackers_dir.iterdir()
        if d.is_dir() and (d / "manifest.yaml").exists()
    )


def _tracker_error_entry(
    tracker: str, last_errors: dict[str, dict], last_run_dt: datetime | None, now: datetime
) -> dict | None:
    """The active (unresolved) error entry for a tracker, if any.

    An error is "active" when the last recorded sync_errors.jsonl failure for
    the tracker is newer than its last recorded success in state/last_run.json
    (or there's no recorded success at all) -- an old error followed by a
    later success is resolved and shouldn't show.
    """
    rec = last_errors.get(tracker)
    if rec is None:
        return None
    error_dt = _parse_ts(rec.get("ts"))
    if error_dt is None or (last_run_dt is not None and error_dt <= last_run_dt):
        return None
    error_text = str(rec.get("error", "")).strip()
    first_line = error_text.splitlines()[0] if error_text else "(no message)"
    tb = str(rec.get("tb", "")).strip()
    full_text = f"{error_text}\n\n{tb}" if tb else error_text
    return {
        "age": humanize_age(now - error_dt),
        "first_line": first_line,
        "full_text": full_text,
    }


def _is_stale(
    schedule: ScheduleSpec | None, last_run_dt: datetime | None, now: datetime
) -> bool:
    """Staleness rule shared by /health, /t/<tracker>, and the dashboard card.

    - `schedule.every` set: stale once `now - last_run` exceeds
      `max(3 * interval, 24h)` -- three missed cycles (with a 24h floor so
      fast-cadence trackers, e.g. every 10m, don't flag on a short blip).
    - No schedule, or a `schedule.cron` we don't parse cadence for (launchd
      owns cron timing, not us): stale when there's no recorded sync at all,
      or the last one is older than 7 days.
    """
    if schedule is not None and schedule.every:
        try:
            interval = _parse_every(schedule.every)
        except ValueError:
            interval = None
        if interval is not None:
            threshold = max(interval * 3, timedelta(hours=24))
            return last_run_dt is None or (now - last_run_dt) > threshold
    return last_run_dt is None or (now - last_run_dt) > timedelta(days=7)


def tracker_status_map(cfg: Config) -> dict[str, dict]:
    """Per-installed-tracker sync status: {slug: {error, last_sync_age, stale}}.

    Shared by /health, /t/<tracker>, and the dashboard's Tracker Health card
    so all three surfaces agree on what "failing" and "stale" mean. `error`
    has the same shape produced by `build_health_page_data` ({"age",
    "first_line", "full_text"}). A tracker with an active `error` is never
    also marked `stale` -- the error is the more specific, more actionable
    signal, so it wins.
    """
    last_run_path = cfg.state_dir / "last_run.json"
    last_runs: dict[str, str] = {}
    if last_run_path.exists():
        try:
            last_runs = json.loads(last_run_path.read_text())
        except json.JSONDecodeError:
            last_runs = {}

    last_errors = _last_errors_by_tracker(cfg)
    now = datetime.now(timezone.utc)

    result: dict[str, dict] = {}
    for tracker in _installed_trackers(cfg):
        last_run_dt = _parse_ts(last_runs.get(tracker))
        last_sync_age = humanize_age(now - last_run_dt) if last_run_dt else None

        schedule = None
        try:
            manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
            schedule = manifest.schedule
        except ManifestError:
            pass

        error_entry = _tracker_error_entry(tracker, last_errors, last_run_dt, now)
        stale = error_entry is None and _is_stale(schedule, last_run_dt, now)

        result[tracker] = {
            "error": error_entry,
            "last_sync_age": last_sync_age,
            "stale": stale,
        }

    return result


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
    statuses = tracker_status_map(cfg)

    last_run_path = cfg.state_dir / "last_run.json"
    last_runs: dict[str, str] = {}
    if last_run_path.exists():
        try:
            last_runs = json.loads(last_run_path.read_text())
        except json.JSONDecodeError:
            last_runs = {}

    now = datetime.now(timezone.utc)
    rows = []
    for tracker in _installed_trackers(cfg):
        title = humanize_tracker_name(tracker)
        last_run_dt = _parse_ts(last_runs.get(tracker))
        status = statuses.get(tracker, {})

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

        rows.append(
            {
                "slug": tracker,
                "title": title,
                "last_sync_age": status.get("last_sync_age"),
                "schedule": schedule_text,
                "error": status.get("error"),
                "stale": status.get("stale", False),
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
    statuses = tracker_status_map(cfg)
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
        status = statuses.get(tracker, {})
        row_class = ""
        age_cell = escape(age)
        if status.get("error"):
            row_class = ' class="health-card-row-error"'
            age_cell = f'{age_cell} <span class="health-card-flag">failing</span>'
        elif status.get("stale"):
            row_class = ' class="health-card-row-stale"'
        rows.append(
            f"<tr{row_class}><td>{name_link}</td>"
            f"<td>{age_cell}</td>"
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
