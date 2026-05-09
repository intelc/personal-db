"""Pure-function interval materializer.

Walks a single session's events (ordered by timestamp ascending) and emits
one interval row per gap between adjacent state-transition events.

State after each event:
  session_start    -> awaiting_user (session is alive, no prompt yet)
  prompt_submitted -> agent_running
  awaiting_user    -> awaiting_user
  session_ended    -> closes session

If no session_ended is present and the last event is older than 60 minutes
(per `now`), emit a synthetic session_ended at last_event_ts + 1 second.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# State the session is in *after* a given event_type fires.
_STATE_AFTER = {
    "session_start": "awaiting_user",
    "prompt_submitted": "agent_running",
    "awaiting_user": "awaiting_user",
    "session_ended": None,  # session is over
}

_STALENESS_THRESHOLD = timedelta(minutes=60)


def _parse_ts(s: str) -> datetime:
    # Accept both "Z" and "+00:00" suffixes.
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _format_ts(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def materialize_intervals(events: list[dict], *, now: datetime) -> list[dict]:
    """events must all share the same (agent, session_id) and be sorted by timestamp."""
    if len(events) < 2:
        # Need at least two events to define an interval's start and end.
        return []

    has_end = any(e["event_type"] == "session_ended" for e in events)
    last_ts = _parse_ts(events[-1]["timestamp"])
    use_events = list(events)

    # Stale with no explicit end: synthesize a close so the open run gets a
    # final interval. The interval's state reflects whatever the session was
    # in at last_event (e.g. agent_running if the agent never finished).
    if not has_end and now - last_ts > _STALENESS_THRESHOLD:
        synthetic_close = {
            **events[-1],
            "timestamp": _format_ts(last_ts + timedelta(seconds=1)),
            "event_type": "session_ended",
            "raw": '{"synthetic":true}',
        }
        use_events.append(synthetic_close)

    intervals: list[dict] = []
    for prev, curr in zip(use_events, use_events[1:]):
        state = _STATE_AFTER.get(prev["event_type"])
        if state is None:
            continue
        start = _parse_ts(prev["timestamp"])
        end = _parse_ts(curr["timestamp"])
        intervals.append(
            {
                "agent": prev["agent"],
                "session_id": prev["session_id"],
                "start_ts": prev["timestamp"],
                "end_ts": curr["timestamp"],
                "state": state,
                "duration_seconds": (end - start).total_seconds(),
                "cwd": prev.get("cwd"),
                "git_branch": prev.get("git_branch"),
            }
        )
    return intervals
