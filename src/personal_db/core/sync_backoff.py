"""Escalating retry backoff for the sync scheduler, terminating in a paused state.

Problem: `sync_due` (see `core.sync`) retries every due tracker on every
scheduler tick with no memory of past failures. A persistently broken source
(an OAuth tracker with a revoked refresh token, an API key that got rotated,
etc.) fails identically forever — burning a sync attempt, and a
`sync_errors.jsonl` entry, every tick. In practice this has produced runs of
dozens to well over a thousand identical failures for a single tracker over
weeks/months with zero chance any of them would ever succeed on their own.

The ladder (`retry_delay`): the first couple of failures cost nothing extra —
a transient blip (network hiccup, a rate limit) should still retry at the
tracker's normal cadence. From there the delay between retries escalates:

    consecutive_failures   delay before next retry
    ---------------------   -----------------------
    1-2                     none (normal cadence)
    3                       30 minutes
    4                       2 hours
    5                       8 hours
    >= PAUSE_AFTER (6)      paused — no more auto-retries, ever

Reaching `PAUSE_AFTER` consecutive failures is a *terminal* state, not just a
longer delay: `sync_due` will never again attempt that tracker on its own.
This is deliberate — an escalating-but-eventually-infinite retry still burns
scheduler ticks and log lines forever for a source that isn't coming back
without user intervention (re-running OAuth, fixing a credential, etc.).

Resume path: there is no explicit "unpause" call. `sync_one` — the function
behind a manual `personal-db sync <tracker>`, the daemon's `POST
/api/sync/<tracker>`, and the settings page's test-sync button — is never
gated by this module; it always runs. A successful manual sync calls
`record_success`, which deletes the tracker's backoff entry outright,
resetting `consecutive_failures` to zero and clearing `paused`. That manual
success is the *only* way a paused tracker resumes automatic syncing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_db.core.config import Config

#: Consecutive failures at which a tracker is marked paused and `sync_due`
#: stops auto-retrying it until a manual sync succeeds.
PAUSE_AFTER = 6

#: Escalation ladder for the delay before the next auto-retry is allowed,
#: keyed by `consecutive_failures`. Values not present here (1, 2, and
#: anything >= PAUSE_AFTER) are handled directly in `retry_delay`.
_LADDER: dict[int, timedelta] = {
    3: timedelta(minutes=30),
    4: timedelta(hours=2),
    5: timedelta(hours=8),
}


def _state_path(cfg: Config) -> Path:
    return cfg.state_dir / "sync_backoff.json"


def _read_state(cfg: Config) -> dict[str, dict]:
    p = _state_path(cfg)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _write_state(cfg: Config, data: dict[str, dict]) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    path = _state_path(cfg)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def retry_delay(consecutive_failures: int) -> timedelta | None:
    """Delay required before the next auto-retry, given a tracker's current
    `consecutive_failures` count. Returns `None` once the tracker should be
    paused instead of delayed (`consecutive_failures >= PAUSE_AFTER`)."""
    if consecutive_failures >= PAUSE_AFTER:
        return None
    return _LADDER.get(consecutive_failures, timedelta(0))


def _blocked_reason_for_entry(entry: dict | None, now: datetime) -> str | None:
    """Pure decision logic given a tracker's persisted backoff entry (or
    `None` if it has no entry at all). Split out from `blocked_reason` so the
    decision logic unit-tests without touching disk."""
    if not entry:
        return None
    if entry.get("paused"):
        return "paused"
    consecutive = entry.get("consecutive_failures", 0)
    last_failure = entry.get("last_failure_ts")
    if consecutive <= 0 or not last_failure:
        return None
    delay = retry_delay(consecutive)
    if delay is None:
        # Defensive: record_failure sets paused=True as soon as the count
        # reaches PAUSE_AFTER, so this shouldn't happen in practice, but stay
        # consistent if state is ever hand-edited or from an older version.
        return "paused"
    last_failure_dt = datetime.fromisoformat(last_failure)
    if now < last_failure_dt + delay:
        return "backoff"
    return None


def blocked_reason(cfg: Config, name: str, now: datetime) -> str | None:
    """Whether `name` may be auto-synced right now.

    Returns `None` if it may, `"paused"` if it has hit `PAUSE_AFTER`
    consecutive failures, or `"backoff"` if it's still within its escalation
    delay from the last failure. Used by `sync_due` only — `sync_one` (manual
    syncs, the settings-page test sync) is never gated by this.
    """
    entry = _read_state(cfg).get(name)
    return _blocked_reason_for_entry(entry, now)


def record_failure(cfg: Config, name: str) -> None:
    """Increment `name`'s consecutive-failure count and stamp the failure
    time. Sets `paused=True` once the count reaches `PAUSE_AFTER`."""
    state = _read_state(cfg)
    entry = state.get(name, {"consecutive_failures": 0, "last_failure_ts": None, "paused": False})
    consecutive = entry.get("consecutive_failures", 0) + 1
    state[name] = {
        "consecutive_failures": consecutive,
        "last_failure_ts": datetime.now(UTC).isoformat(),
        "paused": consecutive >= PAUSE_AFTER,
    }
    _write_state(cfg, state)


def paused_trackers(cfg: Config) -> list[str]:
    """Names of every tracker currently paused. A paused tracker stops
    generating new `sync_errors.jsonl` records (that's the whole point --
    `sync_due` no longer attempts it), so anything that infers "still
    failing" from *recent* error records (e.g.
    `builtin_viz.repeated_failure_trackers`'s trailing time window) needs
    this list too, or a long-paused tracker would silently drop out of view
    once its last real failure ages out of the window -- even though it's
    still failing from the user's perspective, just not being retried."""
    return sorted(name for name, entry in _read_state(cfg).items() if entry.get("paused"))


def tracker_state(cfg: Config, name: str) -> dict | None:
    """`name`'s raw backoff entry (`consecutive_failures`/`last_failure_ts`/
    `paused`), or `None` if it has none. Read-only accessor for callers
    outside this module (e.g. the settings-page overview) that want to
    display backoff/pause state without reaching into the persisted JSON
    directly."""
    return _read_state(cfg).get(name)


def record_success(cfg: Config, name: str) -> None:
    """Clear `name`'s backoff entry entirely — the resume mechanism. Any
    successful `sync_one` call (manual or otherwise) resets the counter and
    unpauses the tracker."""
    state = _read_state(cfg)
    if name in state:
        del state[name]
        _write_state(cfg, state)
