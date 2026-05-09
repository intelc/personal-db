from __future__ import annotations

from datetime import datetime, timedelta, timezone

from personal_db.templates.trackers.code_agent_activity.intervals import (
    materialize_intervals,
)


def _ev(ts: str, event_type: str, agent: str = "claude_code", session_id: str = "s1") -> dict:
    return {
        "agent": agent,
        "session_id": session_id,
        "timestamp": ts,
        "event_type": event_type,
        "cwd": "/tmp/p",
        "git_branch": "main",
        "source_file": None,
        "raw": "{}",
    }


def test_clean_session_three_intervals() -> None:
    events = [
        _ev("2026-05-09T10:00:00.000+00:00", "session_start"),
        _ev("2026-05-09T10:00:05.000+00:00", "prompt_submitted"),
        _ev("2026-05-09T10:00:30.000+00:00", "awaiting_user"),
        _ev("2026-05-09T10:05:00.000+00:00", "session_ended"),
    ]
    intervals = materialize_intervals(events, now=datetime(2026, 5, 9, 11, 0, 0, tzinfo=timezone.utc))

    assert len(intervals) == 3
    assert [i["state"] for i in intervals] == ["awaiting_user", "agent_running", "awaiting_user"]
    # session_start..prompt_submitted = awaiting_user (waiting for first prompt)
    assert intervals[0]["start_ts"] == "2026-05-09T10:00:00.000+00:00"
    assert intervals[0]["end_ts"] == "2026-05-09T10:00:05.000+00:00"
    # prompt_submitted..awaiting_user = agent_running
    assert intervals[1]["start_ts"] == "2026-05-09T10:00:05.000+00:00"
    assert intervals[1]["end_ts"] == "2026-05-09T10:00:30.000+00:00"
    # awaiting_user..session_ended = awaiting_user
    assert intervals[2]["start_ts"] == "2026-05-09T10:00:30.000+00:00"
    assert intervals[2]["end_ts"] == "2026-05-09T10:05:00.000+00:00"


def test_durations_computed() -> None:
    events = [
        _ev("2026-05-09T10:00:00.000+00:00", "session_start"),
        _ev("2026-05-09T10:00:10.000+00:00", "prompt_submitted"),
        _ev("2026-05-09T10:01:00.000+00:00", "session_ended"),
    ]
    intervals = materialize_intervals(events, now=datetime(2026, 5, 9, 11, 0, 0, tzinfo=timezone.utc))
    assert intervals[0]["duration_seconds"] == 10.0
    assert intervals[1]["duration_seconds"] == 50.0


def test_stale_session_gets_synthetic_close() -> None:
    """No session_ended, last event > 60min ago: emit synthetic close at last+1s."""
    events = [
        _ev("2026-05-09T10:00:00.000+00:00", "session_start"),
        _ev("2026-05-09T10:00:05.000+00:00", "prompt_submitted"),
    ]
    now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)  # 2 hours later
    intervals = materialize_intervals(events, now=now)

    # Last interval should close at last_event + 1s with state agent_running (last known)
    assert intervals[-1]["end_ts"] == "2026-05-09T10:00:06.000+00:00"
    assert intervals[-1]["state"] == "agent_running"


def test_recent_open_session_kept_open() -> None:
    """Session with no end and last event < 60min ago: materialize up to last event."""
    events = [
        _ev("2026-05-09T10:00:00.000+00:00", "session_start"),
        _ev("2026-05-09T10:00:05.000+00:00", "prompt_submitted"),
    ]
    now = datetime(2026, 5, 9, 10, 30, 0, tzinfo=timezone.utc)  # 30 min later
    intervals = materialize_intervals(events, now=now)

    # No synthetic close yet — interval extends only to last known event timestamp
    assert intervals[-1]["end_ts"] == "2026-05-09T10:00:05.000+00:00"


def test_intervals_chain_without_gaps() -> None:
    """Property: every interval's end_ts equals the next interval's start_ts."""
    events = [
        _ev("2026-05-09T10:00:00.000+00:00", "session_start"),
        _ev("2026-05-09T10:00:05.000+00:00", "prompt_submitted"),
        _ev("2026-05-09T10:00:10.000+00:00", "awaiting_user"),
        _ev("2026-05-09T10:00:15.000+00:00", "prompt_submitted"),
        _ev("2026-05-09T10:00:20.000+00:00", "awaiting_user"),
        _ev("2026-05-09T10:01:00.000+00:00", "session_ended"),
    ]
    intervals = materialize_intervals(events, now=datetime(2026, 5, 9, 11, 0, tzinfo=timezone.utc))
    for a, b in zip(intervals, intervals[1:]):
        assert a["end_ts"] == b["start_ts"]


def test_empty_events_empty_intervals() -> None:
    assert materialize_intervals([], now=datetime.now(timezone.utc)) == []


def test_single_event_no_intervals() -> None:
    """Need at least two events to define an interval."""
    events = [_ev("2026-05-09T10:00:00.000+00:00", "session_start")]
    # Single event < 60min ago — no synthetic close, no intervals
    now = datetime(2026, 5, 9, 10, 5, 0, tzinfo=timezone.utc)
    assert materialize_intervals(events, now=now) == []
