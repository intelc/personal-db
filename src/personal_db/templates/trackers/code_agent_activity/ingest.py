"""code_agent_activity tracker — sync entry point.

Reads two sources:
  1. ~/personal_db/state/code_agent_hooks.jsonl (Claude Code hook events)
  2. ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl (Codex CLI session logs)

Cursor state (single JSON value stored under the tracker's name in
state/cursors.sqlite):

  {
    "claude_hooks_offset": <int byte offset>,
    "codex_files": {"<abs_path>": <int byte offset>}
  }
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from personal_db.tracker import Tracker

# Sibling modules (parsers.py, intervals.py) need to load both when this file
# is imported as a member of the personal_db package (tests) AND when the
# daemon loads it via importlib.util from <root>/trackers/code_agent_activity/
# (production — see personal_db.sync._load_ingest_module). Direct relative
# imports break the second case. Load siblings explicitly by file path:
import importlib.util as _ilu


def _load_sibling(name: str):
    here = Path(__file__).parent
    spec = _ilu.spec_from_file_location(f"_pdb_code_agent_{name}", here / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(
            f"code_agent_activity: cannot load sibling {name}.py from {here}"
        )
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_parsers = _load_sibling("parsers")
_intervals = _load_sibling("intervals")
parse_claude_hook_line = _parsers.parse_claude_hook_line
parse_codex_event = _parsers.parse_codex_event
materialize_intervals = _intervals.materialize_intervals

log = logging.getLogger(__name__)


def _hooks_log_path() -> Path:
    override = os.environ.get("PERSONAL_DB_HOOKS_LOG")
    if override:
        return Path(override)
    root = Path(os.environ.get("PERSONAL_DB_ROOT") or "~/personal_db").expanduser()
    return root / "state" / "code_agent_hooks.jsonl"


def _codex_sessions_root() -> Path:
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "sessions"


def _load_cursor(t: Tracker) -> dict:
    raw = t.cursor.get()
    if not raw:
        return {"claude_hooks_offset": 0, "codex_files": {}}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("code_agent_activity: cursor unparseable, resetting")
        return {"claude_hooks_offset": 0, "codex_files": {}}


def _save_cursor(t: Tracker, state: dict) -> None:
    t.cursor.set(json.dumps(state))


def _read_claude_hooks(path: Path, offset: int) -> tuple[list[dict], int, int]:
    """Returns (events, new_offset, skipped_lines)."""
    if not path.exists():
        # Preserve the incoming offset so a temporarily-absent log doesn't
        # silently reset the cursor.
        return [], offset, 0
    file_size = path.stat().st_size
    if offset > file_size:
        log.warning("code_agent_activity: hooks log shrank, resetting cursor")
        offset = 0

    events: list[dict] = []
    skipped = 0
    with path.open("rb") as fh:
        fh.seek(offset)
        for raw in fh:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line.strip():
                continue
            ev = parse_claude_hook_line(line)
            if ev is None:
                # Could be malformed or just PreToolUse/PostToolUse — only count
                # malformed JSON as skipped.
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                continue
            events.append(ev)
        new_offset = fh.tell()
    return events, new_offset, skipped


def _read_codex_rollouts(
    root: Path, file_offsets: dict[str, int]
) -> tuple[list[dict], dict[str, int], int]:
    """Walk rollout-*.jsonl files. Returns (events, new_file_offsets, skipped)."""
    if not root.exists():
        return [], file_offsets, 0

    events: list[dict] = []
    # Start from the existing offsets so files that rglob transiently misses
    # don't lose their cursor. We'll overwrite entries for files we successfully
    # process below.
    new_offsets: dict[str, int] = dict(file_offsets)
    skipped = 0

    rollout_paths = sorted(root.rglob("rollout-*.jsonl"))
    for path in rollout_paths:
        key = str(path)
        offset = file_offsets.get(key, 0)
        size = path.stat().st_size
        if offset > size:
            offset = 0
        if offset == size:
            new_offsets[key] = offset
            continue

        # session_id is threaded from the last seen session_meta in this file
        session_id: str | None = None
        # If we've already processed any of this file, we need session_id from
        # an earlier session_meta — re-scan from byte 0 for it (cheap; one row).
        if offset > 0:
            with path.open("rb") as fh:
                head = fh.read(offset).decode("utf-8", errors="replace")
                for line in head.splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict) and row.get("type") == "session_meta":
                        # NOTE: the Codex parser uses payload.id (not payload.session_id)
                        # — see Task 3 for the verified key name.
                        session_id = (row.get("payload") or {}).get("id")
                        break  # one session_meta per file in well-formed Codex output

        with path.open("rb") as fh:
            fh.seek(offset)
            for raw in fh:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not line.strip():
                    continue
                ev = parse_codex_event(line, source_file=key, session_id=session_id)
                if ev is None:
                    try:
                        json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                    continue
                if ev["event_type"] == "session_start":
                    session_id = ev["session_id"]
                events.append(ev)
            new_offsets[key] = fh.tell()

    return events, new_offsets, skipped


def _store_raw_enabled(t: Tracker) -> bool:
    """Whether to store the original line in events.raw.

    The manifest's `config:` block is currently dropped by the Manifest loader
    (no `Manifest.config` field yet — see Task 5 design notes). This function
    exists as forward scaffolding so future wiring of the config block is
    additive, not invasive. Always returns True today.
    """
    cfg_block = getattr(t.manifest, "config", None) or {}
    if isinstance(cfg_block, dict):
        spec = cfg_block.get("store_raw")
        if isinstance(spec, dict):
            return bool(spec.get("default", True))
    return True


def _ensure_schema_columns(con: sqlite3.Connection) -> None:
    """Idempotent schema migration: add new columns to existing installs.

    SQLite's CREATE TABLE IF NOT EXISTS is a no-op on existing tables, so it
    won't introduce columns we add later. Use PRAGMA table_info to check
    and ALTER if missing. Safe to call on every sync; cheap when columns
    already exist.
    """
    for table in ("code_agent_events", "code_agent_intervals"):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and "is_remote" not in cols:
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN is_remote INTEGER NOT NULL DEFAULT 0"
            )
    con.commit()


def _materialize_for_changed_sessions(t: Tracker, new_events: list[dict]) -> int:
    """Rebuild intervals for every (agent, session_id) that got new events.

    Note: this opens a separate sqlite connection from t.upsert(), so a crash
    between the two commits leaves intervals temporarily stale. Self-healing
    on the next sync that touches the affected session, but a session with no
    further events would never re-materialize. Acceptable for v1; revisit if
    we move to a single shared connection per sync.
    """
    if not new_events:
        return 0

    changed_keys = {(e["agent"], e["session_id"]) for e in new_events}
    now = datetime.now(timezone.utc)
    total = 0

    con = sqlite3.connect(t.cfg.db_path)
    try:
        _ensure_schema_columns(con)
        for agent, sid in changed_keys:
            con.execute(
                "DELETE FROM code_agent_intervals WHERE agent=? AND session_id=?",
                (agent, sid),
            )
            rows = con.execute(
                "SELECT agent, session_id, timestamp, event_type, cwd, git_branch, "
                "source_file, raw, is_remote "
                "FROM code_agent_events WHERE agent=? AND session_id=? ORDER BY timestamp",
                (agent, sid),
            ).fetchall()
            session_events = [
                {
                    "agent": r[0],
                    "session_id": r[1],
                    "timestamp": r[2],
                    "event_type": r[3],
                    "cwd": r[4],
                    "git_branch": r[5],
                    "source_file": r[6],
                    "raw": r[7],
                    "is_remote": r[8],
                }
                for r in rows
            ]
            # Codex CLI rollout files emit multiple session_meta rows per
            # session (resume points). Keep only the earliest session_start
            # so the materializer doesn't see spurious agent_running ->
            # awaiting_user transitions mid-session.
            seen_start = False
            deduped: list[dict] = []
            for ev in session_events:
                if ev["event_type"] == "session_start":
                    if seen_start:
                        continue
                    seen_start = True
                deduped.append(ev)
            # Carry the session's remote status onto every interval. SSH state
            # is uniform within a Claude session; for Codex it's always 0
            # (heuristic flag is applied at viz time, not stored).
            session_remote = 1 if any(e.get("is_remote") for e in deduped) else 0
            intervals = materialize_intervals(deduped, now=now)
            total += len(intervals)
            for iv in intervals:
                iv["is_remote"] = session_remote
                con.execute(
                    "INSERT OR REPLACE INTO code_agent_intervals "
                    "(agent, session_id, start_ts, end_ts, state, duration_seconds, "
                    "cwd, git_branch, is_remote) "
                    "VALUES (:agent, :session_id, :start_ts, :end_ts, :state, "
                    ":duration_seconds, :cwd, :git_branch, :is_remote)",
                    iv,
                )
        con.commit()
    finally:
        con.close()
    return total


def sync(t: Tracker) -> dict:
    state = _load_cursor(t)
    keep_raw = _store_raw_enabled(t)

    # Schema migration: ensure new columns exist on existing installs.
    # Must run BEFORE t.upsert writes the rows (which may include new columns).
    _con = sqlite3.connect(t.cfg.db_path)
    try:
        _ensure_schema_columns(_con)
    finally:
        _con.close()

    # Claude hooks
    claude_events, new_claude_offset, claude_skipped = _read_claude_hooks(
        _hooks_log_path(), state.get("claude_hooks_offset", 0)
    )

    # Codex rollouts
    codex_events, new_codex_offsets, codex_skipped = _read_codex_rollouts(
        _codex_sessions_root(), state.get("codex_files", {})
    )

    all_events = claude_events + codex_events
    if not keep_raw:
        for ev in all_events:
            ev["raw"] = None

    inserted = t.upsert(
        "code_agent_events",
        all_events,
        key=["agent", "session_id", "timestamp", "event_type"],
    )
    intervals_n = _materialize_for_changed_sessions(t, all_events)

    state["claude_hooks_offset"] = new_claude_offset
    state["codex_files"] = new_codex_offsets
    _save_cursor(t, state)

    return {
        "claude_events": len(claude_events),
        "codex_events": len(codex_events),
        "events_upserted": inserted,
        "intervals_materialized": intervals_n,
        "skipped_lines": claude_skipped + codex_skipped,
    }


def backfill(t: Tracker, start: str | None = None, end: str | None = None) -> dict:
    """Reset cursors and re-ingest everything. start/end are advisory only —
    we don't filter the events log by date; idempotent upsert handles dupes."""
    t.cursor.set(json.dumps({"claude_hooks_offset": 0, "codex_files": {}}))
    return sync(t)
