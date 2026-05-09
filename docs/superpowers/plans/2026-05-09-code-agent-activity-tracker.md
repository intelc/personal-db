# Code-agent activity tracker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `code_agent_activity` tracker that records session-level state intervals (`agent_running`, `awaiting_user`, `inactive`) for Claude Code (via hooks) and Codex CLI (via rollout JSONL parsing), plus the minimal generic infrastructure (one CLI subcommand, one daemon endpoint, two setup-wizard step kinds) the tracker needs.

**Architecture:** Pure-function parsers and materializer at the core (Tasks 1–4) so the logic is unit-testable in isolation. The tracker (Tasks 5–6) is a thin shell that wires parsers + cursor + upsert. Hook installation is a tracker-provided `actions.py` (Task 7) executed via a generic daemon endpoint (Task 8), surfaced through new setup-wizard step kinds (Task 9). Visualizations (Task 10) and an end-to-end integration test (Task 11) close it out.

**Tech Stack:** Python 3.12, FastAPI (existing daemon), pytest, SQLite (single file at `~/personal_db/db.sqlite`), pure stdlib for the hook writer.

**Spec:** [docs/superpowers/specs/2026-05-09-code-agent-activity-tracker-design.md](../specs/2026-05-09-code-agent-activity-tracker-design.md)

---

## Setup

The plan assumes you are working in the `personal_db` repo with the existing virtualenv at `.venv/`. No worktree required — work on a feature branch on `main`.

```bash
git checkout -b feat/code-agent-activity-tracker
```

---

## Task 1: Hook-payload writer CLI

A tiny stdlib-only typer subcommand that reads a Claude Code hook payload from stdin and atomically appends one JSONL line to `~/personal_db/state/code_agent_hooks.jsonl`. Always exits 0; failures go to stderr only. This must never break Claude Code.

**Files:**
- Create: `src/personal_db/cli/code_agent_hook_cmd.py`
- Create: `src/personal_db/__main__.py` (so `python -m personal_db ...` works)
- Modify: `src/personal_db/cli/main.py` (register subcommand)
- Test: `tests/unit/test_code_agent_hook_writer.py`

> **Why `__main__.py`:** the `_resolve_hook_command` fallback in Task 7 produces `f"{sys.executable} -m personal_db code-agent-hook-write"` for users without `personal-db` on PATH. The package currently has no `__main__.py`, so that invocation fails. The test in this task also uses the `-m personal_db` form, so the `__main__.py` is needed before tests pass.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_code_agent_hook_writer.py`:

```python
from __future__ import annotations

import json
import multiprocessing as mp
import subprocess
import sys
from pathlib import Path


def _run_writer(payload: dict, log_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "personal_db", "code-agent-hook-write"],
        input=json.dumps(payload),
        env={"PERSONAL_DB_HOOKS_LOG": str(log_path), "PATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )


def test_writer_appends_line(tmp_path: Path) -> None:
    log = tmp_path / "code_agent_hooks.jsonl"
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "abc-123",
        "cwd": "/tmp/proj",
    }
    proc = _run_writer(payload, log)
    assert proc.returncode == 0, proc.stderr

    lines = log.read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["hook_event_name"] == "SessionStart"
    assert row["session_id"] == "abc-123"
    assert "received_at" in row  # writer stamps its own arrival ts


def test_writer_appends_to_existing(tmp_path: Path) -> None:
    log = tmp_path / "code_agent_hooks.jsonl"
    log.write_text('{"hook_event_name":"existing"}\n')

    proc = _run_writer({"hook_event_name": "Stop", "session_id": "x"}, log)
    assert proc.returncode == 0

    lines = log.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["hook_event_name"] == "Stop"


def test_writer_exits_zero_on_bad_input(tmp_path: Path) -> None:
    log = tmp_path / "code_agent_hooks.jsonl"
    proc = subprocess.run(
        [sys.executable, "-m", "personal_db", "code-agent-hook-write"],
        input="this is not json",
        env={"PERSONAL_DB_HOOKS_LOG": str(log), "PATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0  # never break Claude Code
    assert proc.stderr  # but log the failure


def _worker(args: tuple[Path, int]) -> None:
    log, n = args
    for i in range(n):
        subprocess.run(
            [sys.executable, "-m", "personal_db", "code-agent-hook-write"],
            input=json.dumps({"hook_event_name": "SessionStart", "session_id": f"s-{i}"}),
            env={"PERSONAL_DB_HOOKS_LOG": str(log), "PATH": ""},
            check=True,
        )


def test_writer_concurrent_writes_do_not_interleave(tmp_path: Path) -> None:
    log = tmp_path / "code_agent_hooks.jsonl"
    workers = 5
    per_worker = 20
    with mp.Pool(workers) as pool:
        pool.map(_worker, [(log, per_worker)] * workers)

    lines = log.read_text().splitlines()
    assert len(lines) == workers * per_worker
    # Every line must be a complete parseable JSON object
    for line in lines:
        json.loads(line)  # raises if interleaved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_hook_writer.py -v`
Expected: FAIL — subcommand doesn't exist yet.

- [ ] **Step 3: Implement the writer subcommand**

Create `src/personal_db/cli/code_agent_hook_cmd.py`:

```python
"""Append-only writer for Claude Code hook payloads.

Invoked by Claude Code hooks (configured async: true) on every lifecycle event.
Reads the hook payload as JSON on stdin, stamps `received_at`, appends one
JSONL line atomically to `~/personal_db/state/code_agent_hooks.jsonl`.

Hard requirement: this must NEVER break Claude Code. Errors go to stderr;
exit code is always 0.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer

app = typer.Typer(help="Internal: append a Claude Code hook payload to the log.")


def _default_log_path() -> Path:
    override = os.environ.get("PERSONAL_DB_HOOKS_LOG")
    if override:
        return Path(override)
    root = Path(os.environ.get("PERSONAL_DB_ROOT") or "~/personal_db").expanduser()
    return root / "state" / "code_agent_hooks.jsonl"


def _append_line(log_path: Path, line: str) -> None:
    """O_APPEND ensures concurrent writes < PIPE_BUF are atomic on POSIX."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


@app.callback(invoke_without_command=True)
def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError(f"hook payload was {type(payload).__name__}, expected object")
        payload["received_at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        _append_line(_default_log_path(), line)
    except Exception as exc:  # noqa: BLE001 — must never propagate
        print(f"code-agent-hook-write: {exc}", file=sys.stderr)
    # Always exit 0
```

- [ ] **Step 4: Register the subcommand and add `__main__.py`**

Open `src/personal_db/cli/main.py` and locate where other CLI subcommands are registered (look for `app.add_typer(...)` calls). Add:

```python
from personal_db.cli import code_agent_hook_cmd

app.add_typer(code_agent_hook_cmd.app, name="code-agent-hook-write")
```

Then create `src/personal_db/__main__.py` (the file does not yet exist):

```python
from personal_db.cli.main import app

if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_hook_writer.py -v`
Expected: PASS — all four tests green.

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/cli/code_agent_hook_cmd.py src/personal_db/cli/main.py src/personal_db/__main__.py tests/unit/test_code_agent_hook_writer.py
git commit -m "feat(code-agent): add hook-payload writer CLI

Reads a Claude Code hook payload on stdin, stamps received_at, atomically
appends one JSONL line to the hooks log. Always exits 0 so a writer fault
cannot block Claude Code. Adds __main__.py so `python -m personal_db ...`
works (used by the hook command fallback when personal-db isn't on PATH).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Claude hook line parser (pure function)

Translate a Claude-Code hook-payload JSON line into a normalized event dict shaped like a `code_agent_events` row. Returns `None` for known-but-unused hook events (`PreToolUse`, `PostToolUse`) and for malformed input.

**Files:**
- Create: `src/personal_db/templates/trackers/code_agent_activity/parsers.py`
- Test: `tests/unit/test_code_agent_parsers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_code_agent_parsers.py`:

```python
from __future__ import annotations

import json

from personal_db.templates.trackers.code_agent_activity.parsers import (
    parse_claude_hook_line,
)


def _line(payload: dict) -> str:
    return json.dumps(payload)


def test_session_start_classified() -> None:
    line = _line(
        {
            "hook_event_name": "SessionStart",
            "session_id": "s1",
            "cwd": "/tmp/p",
            "received_at": "2026-05-09T10:00:00.000+00:00",
        }
    )
    ev = parse_claude_hook_line(line)
    assert ev is not None
    assert ev["agent"] == "claude_code"
    assert ev["session_id"] == "s1"
    assert ev["event_type"] == "session_start"
    assert ev["timestamp"] == "2026-05-09T10:00:00.000+00:00"
    assert ev["cwd"] == "/tmp/p"


def test_user_prompt_submit_to_prompt_submitted() -> None:
    line = _line(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "received_at": "2026-05-09T10:00:01.000+00:00"}
    )
    ev = parse_claude_hook_line(line)
    assert ev["event_type"] == "prompt_submitted"


def test_stop_to_awaiting_user() -> None:
    line = _line({"hook_event_name": "Stop", "session_id": "s1", "received_at": "2026-05-09T10:00:05.000+00:00"})
    ev = parse_claude_hook_line(line)
    assert ev["event_type"] == "awaiting_user"


def test_session_end_to_session_ended() -> None:
    line = _line({"hook_event_name": "SessionEnd", "session_id": "s1", "received_at": "2026-05-09T10:01:00.000+00:00"})
    ev = parse_claude_hook_line(line)
    assert ev["event_type"] == "session_ended"


def test_pre_tool_use_dropped() -> None:
    line = _line({"hook_event_name": "PreToolUse", "session_id": "s1", "received_at": "2026-05-09T10:00:03.000+00:00"})
    assert parse_claude_hook_line(line) is None


def test_post_tool_use_dropped() -> None:
    line = _line({"hook_event_name": "PostToolUse", "session_id": "s1", "received_at": "2026-05-09T10:00:04.000+00:00"})
    assert parse_claude_hook_line(line) is None


def test_malformed_returns_none() -> None:
    assert parse_claude_hook_line("not json") is None
    assert parse_claude_hook_line("{}") is None  # missing hook_event_name
    assert parse_claude_hook_line('{"hook_event_name":"SessionStart"}') is None  # missing session_id


def test_raw_field_is_original_line() -> None:
    line = _line({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00"})
    ev = parse_claude_hook_line(line)
    assert ev["raw"] == line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_parsers.py::test_session_start_classified -v`
Expected: FAIL — module doesn't exist yet.

- [ ] **Step 3: Implement the parser**

Create `src/personal_db/templates/trackers/code_agent_activity/__init__.py` (empty file).

Create `src/personal_db/templates/trackers/code_agent_activity/parsers.py`:

```python
"""Pure-function parsers for the code_agent_activity tracker.

Two entry points:

- parse_claude_hook_line: maps one line of code_agent_hooks.jsonl (written by
  the personal-db code-agent-hook-write CLI) to a normalized event dict, or
  None if the line is malformed or names a hook event we don't classify in v1.

- parse_codex_event: same shape, applied to one JSONL line from a Codex
  rollout file (`event_msg` rows).
"""

from __future__ import annotations

import json

# Maps Claude Code hook_event_name -> our v1 event_type.
# PreToolUse/PostToolUse are intentionally absent; they're forward-compat
# scaffolding (we install the hooks so a future v2 doesn't require re-running
# the installer) but we drop the rows at classification time.
_CLAUDE_EVENT_MAP = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "prompt_submitted",
    "Stop": "awaiting_user",
    "SessionEnd": "session_ended",
}


def parse_claude_hook_line(line: str) -> dict | None:
    """Parse one line of code_agent_hooks.jsonl into a normalized event dict.

    Returns None on:
      - malformed JSON
      - missing required fields (hook_event_name, session_id, received_at)
      - hook_event_name not in the v1 classification (PreToolUse, PostToolUse,
        anything unknown).
    """
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    hook_name = payload.get("hook_event_name")
    event_type = _CLAUDE_EVENT_MAP.get(hook_name)
    if event_type is None:
        return None

    session_id = payload.get("session_id")
    timestamp = payload.get("received_at")
    if not session_id or not timestamp:
        return None

    return {
        "agent": "claude_code",
        "session_id": str(session_id),
        "timestamp": str(timestamp),
        "event_type": event_type,
        "cwd": payload.get("cwd"),
        "git_branch": payload.get("git_branch"),
        "source_file": None,
        "raw": line.rstrip("\n"),
    }


def parse_codex_event(line: str, *, source_file: str | None = None) -> dict | None:
    """Stub — implemented in Task 3."""
    raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_parsers.py -v -k claude`
Expected: PASS — all eight `parse_claude_hook_line` tests green.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/ tests/unit/test_code_agent_parsers.py
git commit -m "feat(code-agent): Claude hook line parser

Pure function: line of code_agent_hooks.jsonl -> event dict (or None for
malformed / known-but-unused hook events).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Codex rollout event parser (pure function)

Translate one `event_msg` line from a Codex CLI rollout JSONL into the same normalized event dict shape. The v1 awaiting-user heuristic is "the last `event_msg` of an assistant turn" — operationally this means we classify based on `payload.type` markers we observe in real rollout files.

**Files:**
- Modify: `src/personal_db/templates/trackers/code_agent_activity/parsers.py` (replace stub)
- Modify: `tests/unit/test_code_agent_parsers.py` (add Codex tests)
- Create: `tests/unit/fixtures/codex_rollout_minimal.jsonl`

- [ ] **Step 1: Write the failing tests**

First, create a synthetic rollout fixture at `tests/unit/fixtures/codex_rollout_minimal.jsonl`. The shape mirrors what we observed in `~/.codex/sessions/2026/05/05/rollout-*.jsonl`:

```jsonl
{"timestamp":"2026-05-05T17:39:05.099Z","type":"session_meta","payload":{"session_id":"019df938-cc02-7c63-9c38-8f40ccca7446","cwd":"/tmp/proj"}}
{"timestamp":"2026-05-05T17:39:05.101Z","type":"event_msg","payload":{"type":"user_message","content":"<redacted>"}}
{"timestamp":"2026-05-05T17:39:05.500Z","type":"event_msg","payload":{"type":"agent_message_delta","delta":"<redacted>"}}
{"timestamp":"2026-05-05T17:39:08.000Z","type":"event_msg","payload":{"type":"task_complete"}}
```

> **Note for the implementer:** these `payload.type` strings (`user_message`, `agent_message_delta`, `task_complete`) are the v1 best-effort guess. Before you write the parser, *read 2–3 real rollout JSONLs from `~/.codex/sessions/`* (most recent dates) and confirm/adjust the actual `payload.type` markers Codex emits. Update the fixture and the parser to match what you see. The test corpus is what we iterate against — that is the spec's stated practice.

Append to `tests/unit/test_code_agent_parsers.py`:

```python
import pathlib

from personal_db.templates.trackers.code_agent_activity.parsers import (
    parse_codex_event,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_codex_session_meta_to_session_start() -> None:
    line = (_FIXTURES / "codex_rollout_minimal.jsonl").read_text().splitlines()[0]
    ev = parse_codex_event(line, source_file="rollout.jsonl")
    assert ev is not None
    assert ev["agent"] == "codex_cli"
    assert ev["event_type"] == "session_start"
    assert ev["session_id"] == "019df938-cc02-7c63-9c38-8f40ccca7446"
    assert ev["timestamp"] == "2026-05-05T17:39:05.099Z"
    assert ev["source_file"] == "rollout.jsonl"


def test_codex_user_message_to_prompt_submitted() -> None:
    line = (_FIXTURES / "codex_rollout_minimal.jsonl").read_text().splitlines()[1]
    # session_id must be threaded from a prior session_meta — parser is per-line, so
    # the caller passes session_id explicitly:
    ev = parse_codex_event(
        line,
        source_file="rollout.jsonl",
        session_id="019df938-cc02-7c63-9c38-8f40ccca7446",
    )
    assert ev["event_type"] == "prompt_submitted"


def test_codex_agent_delta_dropped() -> None:
    """Streaming deltas don't generate state transitions on their own."""
    line = (_FIXTURES / "codex_rollout_minimal.jsonl").read_text().splitlines()[2]
    assert parse_codex_event(line, source_file="rollout.jsonl", session_id="x") is None


def test_codex_task_complete_to_awaiting_user() -> None:
    line = (_FIXTURES / "codex_rollout_minimal.jsonl").read_text().splitlines()[3]
    ev = parse_codex_event(line, source_file="rollout.jsonl", session_id="x")
    assert ev["event_type"] == "awaiting_user"


def test_codex_malformed_returns_none() -> None:
    assert parse_codex_event("not json", source_file="rollout.jsonl") is None
    assert parse_codex_event("{}", source_file="rollout.jsonl") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_parsers.py -v -k codex`
Expected: FAIL — `parse_codex_event` is still a stub.

- [ ] **Step 3: Inspect real rollout files and adjust fixture if needed**

Run:
```bash
ls -t ~/.codex/sessions/2026/*/*/rollout-*.jsonl 2>/dev/null | head -3 | xargs -I{} sh -c 'echo "===== {} ====="; head -10 "{}"'
```

If your observed `payload.type` strings differ from `user_message` / `agent_message_delta` / `task_complete`, update both the fixture and the `_CODEX_PAYLOAD_MAP` you'll write in the next step.

- [ ] **Step 4: Implement the Codex parser**

Replace the stub in `src/personal_db/templates/trackers/code_agent_activity/parsers.py`:

```python
# At the top, add:
_CODEX_PAYLOAD_MAP = {
    # Session boundaries
    "user_message": "prompt_submitted",
    "task_complete": "awaiting_user",
    # Verified against real rollout files; iterate this map against the test
    # corpus, not the spec.
}


def parse_codex_event(
    line: str,
    *,
    source_file: str | None = None,
    session_id: str | None = None,
) -> dict | None:
    """Parse one line of a Codex rollout-*.jsonl into a normalized event dict.

    The caller threads `session_id` from the most recent `session_meta` row in
    the same file (the per-line shape doesn't carry it).

    Returns None on malformed input, on rows that don't represent state
    transitions (streaming deltas, internal events), or when session_id is
    required but missing.
    """
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    timestamp = payload.get("timestamp")
    row_type = payload.get("type")
    inner = payload.get("payload") or {}
    if not timestamp or not row_type:
        return None

    if row_type == "session_meta":
        sid = inner.get("session_id")
        if not sid:
            return None
        return {
            "agent": "codex_cli",
            "session_id": str(sid),
            "timestamp": str(timestamp),
            "event_type": "session_start",
            "cwd": inner.get("cwd"),
            "git_branch": None,
            "source_file": source_file,
            "raw": line.rstrip("\n"),
        }

    if row_type == "event_msg":
        event_type = _CODEX_PAYLOAD_MAP.get(inner.get("type"))
        if event_type is None or not session_id:
            return None
        return {
            "agent": "codex_cli",
            "session_id": str(session_id),
            "timestamp": str(timestamp),
            "event_type": event_type,
            "cwd": None,
            "git_branch": None,
            "source_file": source_file,
            "raw": line.rstrip("\n"),
        }

    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_parsers.py -v`
Expected: PASS — all Claude and Codex tests green.

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/parsers.py tests/unit/test_code_agent_parsers.py tests/unit/fixtures/codex_rollout_minimal.jsonl
git commit -m "feat(code-agent): Codex rollout event parser

Per-line classifier for ~/.codex/sessions/.../rollout-*.jsonl. session_meta
rows produce session_start; event_msg rows produce prompt_submitted /
awaiting_user via a v1 payload.type map iterated against the fixture corpus.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Interval materialization (pure function)

Walks a session's events ordered by timestamp and emits `(start_ts, end_ts, state)` interval rows. Handles the synthetic-`session_ended` rule for sessions whose last event is older than 60 minutes.

**Files:**
- Create: `src/personal_db/templates/trackers/code_agent_activity/intervals.py`
- Create: `tests/unit/test_code_agent_intervals.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_code_agent_intervals.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_intervals.py -v`
Expected: FAIL — module doesn't exist yet.

- [ ] **Step 3: Implement the materializer**

Create `src/personal_db/templates/trackers/code_agent_activity/intervals.py`:

```python
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

from datetime import datetime, timedelta, timezone

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
        # Stale single-event session: synthesize a close so we still get one interval.
        if (
            len(events) == 1
            and now - _parse_ts(events[0]["timestamp"]) > _STALENESS_THRESHOLD
        ):
            # Single event with no peer — there's nothing to define an interval against.
            # Skip; intervals require at least two transitions.
            return []
        return []

    has_end = any(e["event_type"] == "session_ended" for e in events)
    last_ts = _parse_ts(events[-1]["timestamp"])
    use_events = list(events)

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_intervals.py -v`
Expected: PASS — all seven tests green.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/intervals.py tests/unit/test_code_agent_intervals.py
git commit -m "feat(code-agent): interval materialization

Pure function: ordered events for one session -> interval rows. Includes
the synthetic session_ended rule for sessions stale > 60 min.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Tracker manifest and schema

Land the bundled tracker template files so `personal-db tracker install code_agent_activity` works (creates the tables) — even before `ingest.py` exists. This makes Task 6 testable end-to-end against a real installed tracker.

**Files:**
- Create: `src/personal_db/templates/trackers/code_agent_activity/manifest.yaml`
- Create: `src/personal_db/templates/trackers/code_agent_activity/schema.sql`
- Modify: `src/personal_db/installer.py` (extend `_TRACKER_FILES`)

- [ ] **Step 0: Extend the installer's canonical files**

`installer.py` only copies the four canonical files (`manifest.yaml`, `ingest.py`, `schema.sql`, `visualizations.py`) into `<root>/trackers/<name>/`. This tracker also ships `parsers.py`, `intervals.py`, and `actions.py` — without them, the installed copy at runtime breaks. Open `src/personal_db/installer.py` and locate `_TRACKER_FILES` (around line 12). Extend it:

```python
_TRACKER_FILES = (
    "manifest.yaml",
    "ingest.py",
    "schema.sql",
    "visualizations.py",
    "actions.py",     # optional — user-initiated handlers loaded by daemon endpoint
    "parsers.py",     # optional — tracker-specific helper module
    "intervals.py",   # optional — tracker-specific helper module
)
```

The existing `update_template` and hash logic already use `if src_f.is_file()` and treat missing files as empty contributions, so adding these names is benign for trackers that don't ship them.

- [ ] **Step 1: Read an existing manifest for the template's structure**

Run:
```bash
cat src/personal_db/templates/trackers/mosspath_lite/manifest.yaml
```

This shows the `local_only: true`, `permission_type: none`, `schedule`, `time_column`, and (if any) `setup_steps` shape. Use it as the structural model.

- [ ] **Step 2: Write the manifest**

Create `src/personal_db/templates/trackers/code_agent_activity/manifest.yaml`:

```yaml
name: code_agent_activity
description: Coding-agent runtime state — Claude Code (via hooks) and Codex CLI (via rollout JSONL). Records session-level intervals (agent_running, awaiting_user, inactive) so efficiency, engagement, and prompt-cadence questions are answerable without leaving db.sqlite.
permission_type: none
local_only: true
schedule:
  every: 5m
time_column: timestamp
granularity: event

config:
  store_raw:
    type: bool
    default: true
    description: Store the original hook payload / rollout line in the events.raw column. Set false to drop incidental prompt-fragment text from disk.

setup_steps:
  - kind: install_hooks
    title: Install Claude Code hooks
    description: One-click install of SessionStart, UserPromptSubmit, Stop, SessionEnd, PreToolUse, PostToolUse hooks (async; non-blocking) into ~/.claude/settings.json. Existing user hooks are preserved.
  - kind: verify_hooks
    title: Verify hook installation
  - kind: note
    title: Codex CLI requires no setup
    body: Codex CLI activity is read directly from ~/.codex/sessions/. Just keep using `codex` and the next sync will pick it up.

schema:
  tables:
    code_agent_events:
      description: Raw state-transition events
      columns:
        agent: {type: TEXT}
        session_id: {type: TEXT}
        timestamp: {type: TEXT}
        event_type: {type: TEXT}
        cwd: {type: TEXT}
        git_branch: {type: TEXT}
        source_file: {type: TEXT}
        raw: {type: TEXT}
    code_agent_intervals:
      description: Materialized intervals derived from events on each sync
      columns:
        agent: {type: TEXT}
        session_id: {type: TEXT}
        start_ts: {type: TEXT}
        end_ts: {type: TEXT}
        state: {type: TEXT}
        duration_seconds: {type: REAL}
        cwd: {type: TEXT}
        git_branch: {type: TEXT}
```

> **Note:** if the existing tracker manifest schema doesn't have `setup_steps` of kinds `install_hooks` / `verify_hooks` / `note`, the manifest loader will reject them. In that case, look at `src/personal_db/manifest.py` to find the existing `SetupStep` discriminator and either (a) add the three new kinds there in this task, or (b) defer the `setup_steps` block to Task 9 where the wizard wiring is added. The schema and base tracker work either way.

- [ ] **Step 3: Write the schema DDL**

Create `src/personal_db/templates/trackers/code_agent_activity/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS code_agent_events (
  agent       TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  timestamp   TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  cwd         TEXT,
  git_branch  TEXT,
  source_file TEXT,
  raw         TEXT,
  PRIMARY KEY (agent, session_id, timestamp, event_type)
);

CREATE INDEX IF NOT EXISTS idx_code_agent_events_session
  ON code_agent_events(agent, session_id);
CREATE INDEX IF NOT EXISTS idx_code_agent_events_ts
  ON code_agent_events(timestamp);

CREATE TABLE IF NOT EXISTS code_agent_intervals (
  agent            TEXT NOT NULL,
  session_id       TEXT NOT NULL,
  start_ts         TEXT NOT NULL,
  end_ts           TEXT NOT NULL,
  state            TEXT NOT NULL,
  duration_seconds REAL NOT NULL,
  cwd              TEXT,
  git_branch       TEXT,
  PRIMARY KEY (agent, session_id, start_ts)
);

CREATE INDEX IF NOT EXISTS idx_code_agent_intervals_state_ts
  ON code_agent_intervals(state, start_ts);
```

- [ ] **Step 4: Validate the manifest parses**

Run:
```bash
.venv/bin/python -c "from pathlib import Path; from personal_db.manifest import load_manifest; m = load_manifest(Path('src/personal_db/templates/trackers/code_agent_activity/manifest.yaml')); print(m.name, m.schedule.every)"
```

Expected: `code_agent_activity 5m`. If a schema validation error mentions `install_hooks`/`verify_hooks`/`note` step kinds, follow the Step 2 note and either add them to the SetupStep types or trim the `setup_steps` block.

- [ ] **Step 5: Confirm the tracker is auto-discovered**

Run:
```bash
.venv/bin/python -c "from personal_db.installer import list_bundled; print('code_agent_activity' in list_bundled())"
```

Expected: `True`.

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/manifest.yaml src/personal_db/templates/trackers/code_agent_activity/schema.sql src/personal_db/installer.py
# Include manifest.py if you had to add new SetupStep kinds:
git add src/personal_db/manifest.py 2>/dev/null || true
git commit -m "feat(code-agent): tracker manifest and schema

Bundled template for the code_agent_activity tracker — events table,
materialized intervals table, manifest declaring 5m schedule,
local_only=true, store_raw config flag. Extends _TRACKER_FILES so
optional helper modules (parsers/intervals/actions) install alongside
the canonical four.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Tracker ingest

Wire the parsers and materializer together. Reads both sources via cursor-tracked offsets, derives events, materializes intervals, upserts to `~/personal_db/db.sqlite`.

**Files:**
- Create: `src/personal_db/templates/trackers/code_agent_activity/ingest.py`
- Create: `tests/unit/test_code_agent_ingest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_code_agent_ingest.py`:

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from personal_db.config import Config
from personal_db.tracker import Tracker
from personal_db.installer import install_template


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    root = tmp_path / "personal_db"
    cfg = Config(root=root)
    cfg.ensure_dirs()
    install_template("code_agent_activity", cfg)
    # Apply schema.sql
    schema_sql = (cfg.trackers_dir / "code_agent_activity" / "schema.sql").read_text()
    con = sqlite3.connect(cfg.db_path)
    con.executescript(schema_sql)
    con.commit()
    con.close()
    return cfg


def _hooks_log(cfg: Config) -> Path:
    return cfg.state_dir / "code_agent_hooks.jsonl"


def test_sync_ingests_claude_hooks(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(_hooks_log(cfg)))
    log = _hooks_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "\n".join(
            [
                json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00", "cwd": "/p"}),
                json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "received_at": "2026-05-09T10:00:05.000+00:00"}),
                json.dumps({"hook_event_name": "Stop", "session_id": "s1", "received_at": "2026-05-09T10:00:30.000+00:00"}),
                json.dumps({"hook_event_name": "SessionEnd", "session_id": "s1", "received_at": "2026-05-09T10:01:00.000+00:00"}),
                "",
            ]
        )
    )

    from personal_db.templates.trackers.code_agent_activity import ingest
    from personal_db.manifest import load_manifest

    manifest = load_manifest(cfg.trackers_dir / "code_agent_activity" / "manifest.yaml")
    t = Tracker(name="code_agent_activity", cfg=cfg, manifest=manifest)
    ingest.sync(t)

    con = sqlite3.connect(cfg.db_path)
    events = con.execute("SELECT event_type FROM code_agent_events ORDER BY timestamp").fetchall()
    assert [r[0] for r in events] == ["session_start", "prompt_submitted", "awaiting_user", "session_ended"]

    intervals = con.execute(
        "SELECT state, start_ts, end_ts FROM code_agent_intervals ORDER BY start_ts"
    ).fetchall()
    assert [r[0] for r in intervals] == ["awaiting_user", "agent_running", "awaiting_user"]
    con.close()


def test_sync_is_idempotent(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(_hooks_log(cfg)))
    log = _hooks_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00"}) + "\n"
    )

    from personal_db.templates.trackers.code_agent_activity import ingest
    from personal_db.manifest import load_manifest

    manifest = load_manifest(cfg.trackers_dir / "code_agent_activity" / "manifest.yaml")
    t = Tracker(name="code_agent_activity", cfg=cfg, manifest=manifest)

    ingest.sync(t)
    ingest.sync(t)  # second run should be a no-op

    con = sqlite3.connect(cfg.db_path)
    n = con.execute("SELECT COUNT(*) FROM code_agent_events").fetchone()[0]
    assert n == 1


def test_sync_handles_malformed_line(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(_hooks_log(cfg)))
    log = _hooks_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "this is not json\n"
        + json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00"})
        + "\n"
    )

    from personal_db.templates.trackers.code_agent_activity import ingest
    from personal_db.manifest import load_manifest

    manifest = load_manifest(cfg.trackers_dir / "code_agent_activity" / "manifest.yaml")
    t = Tracker(name="code_agent_activity", cfg=cfg, manifest=manifest)
    ingest.sync(t)  # must not raise

    con = sqlite3.connect(cfg.db_path)
    n = con.execute("SELECT COUNT(*) FROM code_agent_events").fetchone()[0]
    assert n == 1


def test_sync_resumes_from_cursor(cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(_hooks_log(cfg)))
    log = _hooks_log(cfg)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "received_at": "2026-05-09T10:00:00.000+00:00"}) + "\n"
    )

    from personal_db.templates.trackers.code_agent_activity import ingest
    from personal_db.manifest import load_manifest

    manifest = load_manifest(cfg.trackers_dir / "code_agent_activity" / "manifest.yaml")
    t = Tracker(name="code_agent_activity", cfg=cfg, manifest=manifest)
    ingest.sync(t)

    # Append a new line; sync should pick up only the new one.
    with log.open("a") as fh:
        fh.write(
            json.dumps(
                {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "received_at": "2026-05-09T10:00:05.000+00:00"}
            )
            + "\n"
        )
    ingest.sync(t)

    con = sqlite3.connect(cfg.db_path)
    n = con.execute("SELECT COUNT(*) FROM code_agent_events").fetchone()[0]
    assert n == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_ingest.py -v`
Expected: FAIL — `ingest.py` doesn't exist yet.

- [ ] **Step 3: Implement ingest.py**

Create `src/personal_db/templates/trackers/code_agent_activity/ingest.py`:

```python
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
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from personal_db.tracker import Tracker

# Sibling modules (parsers.py, intervals.py) need to load both when this file
# is imported as a member of the personal_db package (tests) AND when the
# daemon loads it via importlib.util from <root>/trackers/code_agent_activity/
# (production — see personal_db.sync._load_ingest_module). Direct relative
# imports break the second case. Load siblings explicitly by file path:
import importlib.util as _ilu
from pathlib import Path as _Path


def _load_sibling(name: str):
    here = _Path(__file__).parent
    spec = _ilu.spec_from_file_location(f"_pdb_code_agent_{name}", here / f"{name}.py")
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
        return [], 0, 0
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
    new_offsets: dict[str, int] = {}
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
                        session_id = (row.get("payload") or {}).get("session_id")

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
    cfg_block = getattr(t.manifest, "config", None) or {}
    if isinstance(cfg_block, dict):
        spec = cfg_block.get("store_raw")
        if isinstance(spec, dict):
            return bool(spec.get("default", True))
    return True


def _materialize_for_changed_sessions(t: Tracker, new_events: list[dict]) -> int:
    """Rebuild intervals for every (agent, session_id) that got new events."""
    if not new_events:
        return 0
    import sqlite3

    changed_keys = {(e["agent"], e["session_id"]) for e in new_events}
    now = datetime.now(timezone.utc)
    total = 0

    con = sqlite3.connect(t.cfg.db_path)
    try:
        for agent, sid in changed_keys:
            con.execute(
                "DELETE FROM code_agent_intervals WHERE agent=? AND session_id=?",
                (agent, sid),
            )
            rows = con.execute(
                "SELECT agent, session_id, timestamp, event_type, cwd, git_branch, source_file, raw "
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
                }
                for r in rows
            ]
            intervals = materialize_intervals(session_events, now=now)
            total += len(intervals)
            for iv in intervals:
                con.execute(
                    "INSERT INTO code_agent_intervals (agent, session_id, start_ts, end_ts, state, duration_seconds, cwd, git_branch) "
                    "VALUES (:agent, :session_id, :start_ts, :end_ts, :state, :duration_seconds, :cwd, :git_branch)",
                    iv,
                )
        con.commit()
    finally:
        con.close()
    return total


def sync(t: Tracker) -> dict:
    state = _load_cursor(t)
    keep_raw = _store_raw_enabled(t)

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_ingest.py -v`
Expected: PASS — all four tests green.

- [ ] **Step 5: Smoke against your real data**

```bash
.venv/bin/python -m personal_db --root ~/personal_db tracker reinstall code_agent_activity
.venv/bin/python -m personal_db --root ~/personal_db sync code_agent_activity
sqlite3 ~/personal_db/db.sqlite "SELECT agent, COUNT(*) FROM code_agent_events GROUP BY agent"
sqlite3 ~/personal_db/db.sqlite "SELECT state, COUNT(*) FROM code_agent_intervals GROUP BY state"
```

Expected: Codex rows show up (because rollout files exist), Claude rows are zero (hooks aren't installed yet — Task 9 fixes that). Some intervals materialize for the Codex sessions.

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/ingest.py tests/unit/test_code_agent_ingest.py
git commit -m "feat(code-agent): tracker ingest

sync() reads code_agent_hooks.jsonl and ~/.codex/sessions/ rollout files via
byte-offset cursors, classifies events, upserts code_agent_events, and
re-materializes code_agent_intervals for any session with new rows.
Idempotent on PRIMARY KEY; resilient to malformed lines and shrunken files.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Tracker actions (install/uninstall/verify hooks)

A new file convention: bundled trackers may ship an `actions.py` module exposing one-shot named handlers. The handlers for `code_agent_activity` edit `~/.claude/settings.json` to install our hooks atomically.

**Files:**
- Create: `src/personal_db/templates/trackers/code_agent_activity/actions.py`
- Create: `tests/unit/test_code_agent_actions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_code_agent_actions.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_db.templates.trackers.code_agent_activity import actions


@pytest.fixture
def cfg(tmp_path: Path) -> SimpleNamespace:
    settings = tmp_path / "settings.json"
    return SimpleNamespace(
        claude_settings_path=settings,
        hook_command="personal-db code-agent-hook-write",
    )


def test_install_creates_settings_when_missing(cfg: SimpleNamespace) -> None:
    result = actions.install_hooks(cfg)
    assert result["ok"] is True

    data = json.loads(cfg.claude_settings_path.read_text())
    assert "hooks" in data
    for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PreToolUse", "PostToolUse"):
        assert event in data["hooks"]


def test_install_preserves_existing_user_hooks(cfg: SimpleNamespace) -> None:
    cfg.claude_settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "echo user-hook"}]}
                    ]
                }
            }
        )
    )

    actions.install_hooks(cfg)

    data = json.loads(cfg.claude_settings_path.read_text())
    user_hook_present = any(
        h.get("command") == "echo user-hook"
        for entry in data["hooks"]["SessionStart"]
        for h in entry.get("hooks", [])
    )
    assert user_hook_present


def test_install_is_idempotent(cfg: SimpleNamespace) -> None:
    actions.install_hooks(cfg)
    first = cfg.claude_settings_path.read_text()
    actions.install_hooks(cfg)
    second = cfg.claude_settings_path.read_text()
    assert first == second  # exact same bytes — no duplicate entries


def test_uninstall_removes_only_managed_entries(cfg: SimpleNamespace) -> None:
    cfg.claude_settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "echo user-hook"}]}
                    ]
                }
            }
        )
    )
    actions.install_hooks(cfg)
    actions.uninstall_hooks(cfg)

    data = json.loads(cfg.claude_settings_path.read_text())
    # User hook still there
    remaining = data["hooks"].get("SessionStart", [])
    assert any(
        h.get("command") == "echo user-hook"
        for entry in remaining
        for h in entry.get("hooks", [])
    )
    # Our managed hooks gone
    assert not any(
        h.get("_personal_db_managed")
        for entry in remaining
        for h in entry.get("hooks", [])
    )


def test_verify_reports_installed(cfg: SimpleNamespace) -> None:
    actions.install_hooks(cfg)
    result = actions.verify_hooks(cfg)
    assert result["installed"] is True
    assert result["ours_present"] is True


def test_verify_reports_missing_when_absent(cfg: SimpleNamespace) -> None:
    result = actions.verify_hooks(cfg)
    assert result["ours_present"] is False


def test_install_refuses_malformed_settings(cfg: SimpleNamespace) -> None:
    cfg.claude_settings_path.write_text("not json at all {")
    result = actions.install_hooks(cfg)
    assert result["ok"] is False
    assert "malformed" in result["message"].lower() or "parse" in result["message"].lower()
    # File untouched
    assert cfg.claude_settings_path.read_text() == "not json at all {"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_actions.py -v`
Expected: FAIL — `actions.py` doesn't exist.

- [ ] **Step 3: Implement actions.py**

Create `src/personal_db/templates/trackers/code_agent_activity/actions.py`:

```python
"""User-initiated actions for the code_agent_activity tracker.

Exposed handlers (called via the daemon's POST /api/trackers/{name}/actions/{action}):

  install_hooks(cfg)   — write our hooks block into ~/.claude/settings.json
  uninstall_hooks(cfg) — remove only entries we tagged with _personal_db_managed
  verify_hooks(cfg)    — report whether our hooks are present
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# Each entry is one Claude Code hook command we manage. async: true keeps
# the writer off Claude Code's critical path.
_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PreToolUse", "PostToolUse")
_MANAGED_KEY = "_personal_db_managed"


def _resolve_hook_command(cfg) -> str:
    explicit = getattr(cfg, "hook_command", None)
    if explicit:
        return explicit
    bin_path = shutil.which("personal-db")
    if bin_path:
        return f"{bin_path} code-agent-hook-write"
    return f"{sys.executable} -m personal_db code-agent-hook-write"


def _settings_path(cfg) -> Path:
    explicit = getattr(cfg, "claude_settings_path", None)
    if explicit:
        return Path(explicit)
    return Path("~/.claude/settings.json").expanduser()


def _load_settings(path: Path) -> dict | None:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _managed_entry(command: str) -> dict:
    return {"hooks": [{"type": "command", "command": command, "async": True, _MANAGED_KEY: True}]}


def install_hooks(cfg) -> dict:
    path = _settings_path(cfg)
    settings = _load_settings(path)
    if settings is None:
        return {"ok": False, "message": f"~/.claude/settings.json is malformed JSON; refusing to overwrite. Fix manually then retry."}

    command = _resolve_hook_command(cfg)
    settings.setdefault("hooks", {})
    for event in _HOOK_EVENTS:
        existing = settings["hooks"].setdefault(event, [])
        # Drop any prior managed entries (idempotent reinstall + command-string refresh).
        existing[:] = [
            entry
            for entry in existing
            if not any(h.get(_MANAGED_KEY) for h in entry.get("hooks", []))
        ]
        existing.append(_managed_entry(command))

    _atomic_write(path, settings)
    return {"ok": True, "message": f"Installed {len(_HOOK_EVENTS)} Claude Code hooks via `{command}`."}


def uninstall_hooks(cfg) -> dict:
    path = _settings_path(cfg)
    settings = _load_settings(path)
    if settings is None:
        return {"ok": False, "message": "~/.claude/settings.json is malformed JSON; cannot edit safely."}
    if not settings or "hooks" not in settings:
        return {"ok": True, "message": "No hooks block — nothing to uninstall."}

    removed = 0
    for event in _HOOK_EVENTS:
        existing = settings["hooks"].get(event, [])
        before = len(existing)
        existing[:] = [
            entry
            for entry in existing
            if not any(h.get(_MANAGED_KEY) for h in entry.get("hooks", []))
        ]
        removed += before - len(existing)
        if not existing:
            settings["hooks"].pop(event, None)

    _atomic_write(path, settings)
    return {"ok": True, "message": f"Removed {removed} managed hook entries."}


def verify_hooks(cfg) -> dict:
    path = _settings_path(cfg)
    settings = _load_settings(path)
    if settings is None:
        return {"installed": False, "ours_present": False, "message": "settings.json is malformed."}
    if not path.exists():
        return {"installed": False, "ours_present": False, "message": "settings.json does not exist."}

    hooks = settings.get("hooks", {})
    found = sum(
        1
        for event in _HOOK_EVENTS
        for entry in hooks.get(event, [])
        for h in entry.get("hooks", [])
        if h.get(_MANAGED_KEY)
    )
    ours_present = found >= len(_HOOK_EVENTS)
    return {
        "installed": bool(hooks),
        "ours_present": ours_present,
        "message": f"Found {found}/{len(_HOOK_EVENTS)} managed hook entries.",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_code_agent_actions.py -v`
Expected: PASS — all seven tests green.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/actions.py tests/unit/test_code_agent_actions.py
git commit -m "feat(code-agent): tracker actions for hook install/uninstall/verify

Tracker-provided actions.py module: install_hooks deep-merges our entries
into ~/.claude/settings.json (tagged _personal_db_managed for safe
uninstall), uninstall removes only managed entries, verify reports status.
Atomic write via temp+rename; refuses to clobber malformed JSON.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Generic daemon actions endpoint

Add `POST /api/trackers/{name}/actions/{action}` to the daemon. It dynamically imports the tracker's installed `actions.py` and calls the named handler. Generic — `code_agent_activity` is just the first user.

**Files:**
- Modify: `src/personal_db/daemon/http.py` (add new route)
- Create: `tests/unit/test_daemon_actions_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_daemon_actions_endpoint.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_db.config import Config
from personal_db.daemon.http import build_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    cfg = Config(root=tmp_path / "personal_db")
    cfg.ensure_dirs()

    # Stub installed tracker with an actions.py exposing a known handler.
    tracker_dir = cfg.trackers_dir / "stub"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "manifest.yaml").write_text(
        "name: stub\ndescription: x\npermission_type: none\nschema:\n  tables: {}\n"
    )
    (tracker_dir / "actions.py").write_text(
        "def hello(cfg):\n    return {'ok': True, 'message': 'hi'}\n"
        "def boom(cfg):\n    raise RuntimeError('intentional')\n"
    )

    app = build_app(cfg)
    return TestClient(app)


def test_calls_tracker_action(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/hello")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["message"] == "hi"


def test_unknown_tracker_404(client: TestClient) -> None:
    r = client.post("/api/trackers/no-such-tracker/actions/hello")
    assert r.status_code == 404


def test_unknown_action_404(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/nope")
    assert r.status_code == 404


def test_handler_exception_500_with_message(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/boom")
    assert r.status_code == 500
    assert "intentional" in r.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_actions_endpoint.py -v`
Expected: FAIL — endpoint doesn't exist.

- [ ] **Step 3: Add the endpoint**

In `src/personal_db/daemon/http.py`, locate the `build_app(cfg)` function and where other `/api/...` routes are defined (search for `@app.post("/api/sync/{tracker}")`). Add this route alongside them:

```python
@app.post("/api/trackers/{name}/actions/{action}")
def tracker_action(name: str, action: str):
    import importlib.util
    from fastapi import HTTPException

    tracker_dir = cfg.trackers_dir / name
    actions_path = tracker_dir / "actions.py"
    if not actions_path.exists():
        raise HTTPException(status_code=404, detail=f"tracker '{name}' has no actions.py")

    spec = importlib.util.spec_from_file_location(
        f"_pdb_actions_{name}", actions_path
    )
    if spec is None or spec.loader is None:
        raise HTTPException(status_code=500, detail="failed to load actions module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    handler = getattr(module, action, None)
    if handler is None or not callable(handler):
        raise HTTPException(status_code=404, detail=f"action '{action}' not found on tracker '{name}'")

    try:
        return handler(cfg)
    except Exception as exc:  # noqa: BLE001 — surface to client as 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_actions_endpoint.py -v`
Expected: PASS — all four tests green.

- [ ] **Step 5: Smoke against the real tracker**

Make sure the daemon is running, then:
```bash
curl -s -X POST http://127.0.0.1:8765/api/trackers/code_agent_activity/actions/verify_hooks | python3 -m json.tool
```

Expected: JSON with `{installed: false, ours_present: false, ...}` (we haven't installed yet — Task 9).

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/daemon/http.py tests/unit/test_daemon_actions_endpoint.py
git commit -m "feat(daemon): generic /api/trackers/{name}/actions/{action} endpoint

Dynamic dispatch to a tracker's installed actions.py. Returns the
handler's dict on success, 404 for missing tracker/action, 500 with the
exception message on handler failure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Setup wizard step kinds (install_hooks, verify_hooks, note)

Render the new manifest step kinds in the setup wizard. `install_hooks` is a button that POSTs to the action endpoint and shows the result inline. `verify_hooks` runs once on page load and shows a status badge. `note` is plain explanatory text.

**Files:**
- Modify: `src/personal_db/manifest.py` (if not already done in Task 5: register the step types)
- Modify: `src/personal_db/ui/setup_runner.py` (handle the new kinds in dispatch)
- Modify: `src/personal_db/ui/templates/setup_tracker.html` (render the new kinds)
- Modify: `tests/unit/test_ui_setup.py` (assert the rendered HTML for each kind)

- [ ] **Step 1: Read the existing setup_tracker.html and setup_runner.py**

Look at how an existing step (e.g., the OAuth-flow step or the manual-permission-grant step in `mosspath_lite`) is rendered. The wizard is a Jinja template iterating over `manifest.setup_steps`. Each step has a `kind` field that's used for dispatch.

- [ ] **Step 2: Register the step kinds (if not already)**

In `src/personal_db/manifest.py`, locate the `SetupStep` discriminated union (or equivalent). Add three new variants with their fields:

```python
class InstallHooksStep(BaseModel):
    kind: Literal["install_hooks"]
    title: str
    description: str | None = None

class VerifyHooksStep(BaseModel):
    kind: Literal["verify_hooks"]
    title: str

class NoteStep(BaseModel):
    kind: Literal["note"]
    title: str
    body: str
```

Then add them to the `SetupStep` union. (The exact integration depends on whether the existing union uses `Annotated[Union[...]]` or pydantic's discriminator helpers — match what's there.)

- [ ] **Step 3: Add wizard rendering for the new step kinds**

In `src/personal_db/ui/templates/setup_tracker.html`, find the `{% for step in steps %}` block and add cases for the three new kinds. Match the visual style of existing step blocks; the key behaviors:

```html
{% elif step.kind == "install_hooks" %}
  <div class="setup-step" data-step-kind="install_hooks">
    <h3>{{ step.title }}</h3>
    {% if step.description %}<p>{{ step.description }}</p>{% endif %}
    <button
      class="btn-primary"
      onclick="installHooks(this, '{{ tracker_name }}')"
    >Install hooks</button>
    <pre class="action-output" hidden></pre>
  </div>

{% elif step.kind == "verify_hooks" %}
  <div class="setup-step" data-step-kind="verify_hooks" data-tracker="{{ tracker_name }}">
    <h3>{{ step.title }}</h3>
    <span class="hook-status-badge">checking…</span>
  </div>

{% elif step.kind == "note" %}
  <div class="setup-step setup-step--note">
    <h3>{{ step.title }}</h3>
    <p>{{ step.body }}</p>
  </div>
{% endif %}
```

Add the JS handlers (in the same template's existing `<script>` block, or in `static/setup.js` if that's the existing convention):

```javascript
async function installHooks(button, tracker) {
  button.disabled = true;
  const out = button.parentElement.querySelector('.action-output');
  out.hidden = false;
  out.textContent = "installing…";
  try {
    const r = await fetch(`/api/trackers/${tracker}/actions/install_hooks`, {method: "POST"});
    const body = await r.json();
    out.textContent = body.message || JSON.stringify(body);
    if (!body.ok) out.classList.add("error");
    // Re-run any verify badges on this page.
    document.querySelectorAll(`[data-step-kind="verify_hooks"]`).forEach(refreshHookStatus);
  } catch (err) {
    out.textContent = `Daemon not reachable — run \`personal-db daemon install\``;
    out.classList.add("error");
  } finally {
    button.disabled = false;
  }
}

async function refreshHookStatus(el) {
  const tracker = el.dataset.tracker;
  const badge = el.querySelector(".hook-status-badge");
  try {
    const r = await fetch(`/api/trackers/${tracker}/actions/verify_hooks`, {method: "POST"});
    const body = await r.json();
    badge.textContent = body.ours_present ? "✓ installed" : "✗ not installed";
    badge.className = `hook-status-badge ${body.ours_present ? "ok" : "warn"}`;
  } catch {
    badge.textContent = "daemon unreachable";
    badge.className = "hook-status-badge error";
  }
}

document.querySelectorAll(`[data-step-kind="verify_hooks"]`).forEach(refreshHookStatus);
```

- [ ] **Step 4: Wire setup_runner.py if it has a step dispatcher**

If `setup_runner.py` does any per-kind logic beyond rendering (e.g., marking steps as "complete" once an action runs), add cases for the three new kinds. For our purposes, install_hooks/verify_hooks/note do not block the wizard advancing — they are informational/optional. If the runner has a "step complete?" check, treat `note` as always complete and the others as complete when verify reports `ours_present: true`.

- [ ] **Step 5: Add tests for rendering**

Append to `tests/unit/test_ui_setup.py` (or create the file if it doesn't yet exist):

```python
def test_install_hooks_step_renders_button(setup_template_renders) -> None:
    """setup_template_renders is an existing fixture that builds an in-memory
    Manifest with the given steps and returns the rendered HTML. If it doesn't
    exist, mirror the pattern of the nearest existing rendering test."""
    html = setup_template_renders(
        steps=[{"kind": "install_hooks", "title": "Install hooks", "description": "x"}]
    )
    assert "installHooks(this" in html
    assert "Install hooks" in html


def test_verify_hooks_step_renders_badge(setup_template_renders) -> None:
    html = setup_template_renders(
        steps=[{"kind": "verify_hooks", "title": "Verify"}]
    )
    assert "hook-status-badge" in html


def test_note_step_renders_body(setup_template_renders) -> None:
    html = setup_template_renders(
        steps=[{"kind": "note", "title": "T", "body": "Hello world"}]
    )
    assert "Hello world" in html
```

If `setup_template_renders` is not an existing fixture, look at how other rendering tests in `tests/unit/test_ui_setup.py` build a Manifest in-memory and call `Jinja2Templates(...).get_template("setup_tracker.html").render(...)`. Match that pattern.

- [ ] **Step 6: Run tests to verify they pass**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_ui_setup.py -v
.venv/bin/python -m pytest tests/unit/test_code_agent_actions.py -v
```

Expected: PASS for both.

- [ ] **Step 7: Smoke the wizard manually**

Start the daemon (`personal-db daemon run` in another shell), open `http://127.0.0.1:8765/setup/code_agent_activity`, click "Install hooks", confirm:
1. The output area says "Installed 6 Claude Code hooks via …".
2. The verify badge updates to "✓ installed".
3. `cat ~/.claude/settings.json` shows the new entries with `"_personal_db_managed": true`.

- [ ] **Step 8: Commit**

```bash
git add src/personal_db/manifest.py src/personal_db/ui/setup_runner.py src/personal_db/ui/templates/setup_tracker.html tests/unit/test_ui_setup.py
git add src/personal_db/ui/static/  # if you put JS in a static file
git commit -m "feat(setup): install_hooks / verify_hooks / note step kinds

New manifest setup_step kinds rendered in the wizard. install_hooks calls
the action endpoint via fetch and displays the result inline; verify_hooks
runs on page load and shows an installed/not-installed badge.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Visualizations

Four renderers in the tracker's `visualizations.py`. Existing trackers in this repo use `personal_db.ui.charts` helpers (heatmap, calendar_grid, horizontal_bars). Mirror the conventions used in `mosspath_lite/visualizations.py`.

**Files:**
- Create: `src/personal_db/templates/trackers/code_agent_activity/visualizations.py`

- [ ] **Step 1: Read the closest existing visualizations.py for patterns**

```bash
cat src/personal_db/templates/trackers/mosspath_lite/visualizations.py
```

Note the function signature convention (`render_<name>(cfg: Config) -> str`), how they connect to `cfg.db_path`, what helpers they call from `personal_db.ui.charts`.

- [ ] **Step 2: Write `render_runtime_heatmap`**

Create `src/personal_db/templates/trackers/code_agent_activity/visualizations.py`:

```python
"""Visualizations for the code_agent_activity tracker."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from personal_db.config import Config


def _connect(cfg: Config) -> sqlite3.Connection:
    return sqlite3.connect(cfg.db_path)


def render_runtime_heatmap(cfg: Config) -> str:
    """7-day x 24-hour heatmap of agent_running seconds."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    grid: dict[tuple[int, int], float] = defaultdict(float)

    con = _connect(cfg)
    rows = con.execute(
        """
        SELECT start_ts, end_ts, duration_seconds
        FROM code_agent_intervals
        WHERE state = 'agent_running' AND start_ts >= ?
        """,
        (start.isoformat(),),
    ).fetchall()
    con.close()

    for s_iso, _e_iso, dur in rows:
        s = datetime.fromisoformat(s_iso.replace("Z", "+00:00"))
        local = s.astimezone()
        grid[(local.weekday(), local.hour)] += dur or 0.0

    # Render simple HTML grid; for fancier output use personal_db.ui.charts.heatmap.
    rows_html = []
    for day in range(7):
        cells = []
        for hour in range(24):
            sec = grid.get((day, hour), 0.0)
            mins = int(sec // 60)
            shade = min(255, int(255 * (sec / 1800)))  # 30 min = full shade
            cells.append(
                f'<td title="{mins}m" '
                f'style="background:rgb({255-shade},{255-shade},255);width:18px;height:18px"></td>'
            )
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][day]
        rows_html.append(f"<tr><th>{day_name}</th>{''.join(cells)}</tr>")
    return (
        "<h3>Agent runtime — last 7 days (local time)</h3>"
        f"<table class='heatmap'>{''.join(rows_html)}</table>"
    )


def render_state_breakdown(cfg: Config) -> str:
    """Daily stacked bar: total seconds in each state, last 7 days."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    con = _connect(cfg)
    rows = con.execute(
        """
        SELECT date(start_ts), state, SUM(duration_seconds)
        FROM code_agent_intervals
        WHERE start_ts >= ?
        GROUP BY date(start_ts), state
        """,
        (start.isoformat(),),
    ).fetchall()
    con.close()

    by_day: dict[str, dict[str, float]] = defaultdict(lambda: {"agent_running": 0, "awaiting_user": 0})
    for date_str, state, total in rows:
        by_day[date_str][state] = total or 0

    lines = ["<h3>State breakdown — last 7 days</h3>", "<table>"]
    lines.append("<tr><th>Date</th><th>Running (min)</th><th>Awaiting (min)</th></tr>")
    for day in sorted(by_day):
        run_min = int(by_day[day]["agent_running"] // 60)
        wait_min = int(by_day[day]["awaiting_user"] // 60)
        lines.append(f"<tr><td>{day}</td><td>{run_min}</td><td>{wait_min}</td></tr>")
    lines.append("</table>")
    return "".join(lines)


def render_prompt_cadence(cfg: Config) -> str:
    """Histogram of inter-prompt gap durations, last 7 days."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    con = _connect(cfg)
    rows = con.execute(
        """
        SELECT agent, session_id, timestamp
        FROM code_agent_events
        WHERE event_type = 'prompt_submitted' AND timestamp >= ?
        ORDER BY agent, session_id, timestamp
        """,
        (start.isoformat(),),
    ).fetchall()
    con.close()

    buckets = {"<10s": 0, "10s–1m": 0, "1m–10m": 0, "10m+": 0}
    last_per_session: dict[tuple[str, str], datetime] = {}
    for agent, sid, ts_iso in rows:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        key = (agent, sid)
        if key in last_per_session:
            gap = (ts - last_per_session[key]).total_seconds()
            if gap < 10:
                buckets["<10s"] += 1
            elif gap < 60:
                buckets["10s–1m"] += 1
            elif gap < 600:
                buckets["1m–10m"] += 1
            else:
                buckets["10m+"] += 1
        last_per_session[key] = ts

    lines = ["<h3>Prompt cadence — last 7 days</h3>", "<ul>"]
    for label, count in buckets.items():
        lines.append(f"<li>{label}: {count}</li>")
    lines.append("</ul>")
    return "".join(lines)


def render_engagement(cfg: Config) -> str:
    """Per agent_running interval: keystrokes the user produced during it.

    Requires the mosspath_lite tracker to be installed. Renders a graceful
    fallback if not.
    """
    con = _connect(cfg)
    has_mosspath = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mosspath_lite_events'"
    ).fetchone()
    if not has_mosspath:
        con.close()
        return (
            "<h3>Engagement</h3>"
            "<p><em>Install the mosspath_lite tracker to see engagement data — "
            "this view joins agent runtime intervals against user keystroke batches.</em></p>"
        )

    rows = con.execute(
        """
        SELECT i.agent, i.session_id, i.duration_seconds,
               COALESCE(SUM(m.key_count), 0) AS keystrokes
        FROM code_agent_intervals i
        LEFT JOIN mosspath_lite_events m
          ON m.timestamp >= i.start_ts
         AND m.timestamp <  i.end_ts
         AND m.action_type = 'input_batch'
        WHERE i.state = 'agent_running'
          AND i.start_ts >= datetime('now', '-7 days')
        GROUP BY i.agent, i.session_id, i.start_ts
        ORDER BY i.start_ts DESC
        LIMIT 50
        """,
    ).fetchall()
    con.close()

    if not rows:
        return "<h3>Engagement</h3><p>No agent_running intervals in the last 7 days.</p>"

    lines = ["<h3>Engagement — last 50 agent runs (last 7 days)</h3>", "<table>"]
    lines.append("<tr><th>Agent</th><th>Session</th><th>Run sec</th><th>Keys during</th><th>Keys/sec</th></tr>")
    for agent, sid, dur, keys in rows:
        rate = (keys / dur) if dur else 0.0
        lines.append(
            f"<tr><td>{agent}</td><td>{sid[:8]}</td><td>{int(dur)}</td>"
            f"<td>{keys}</td><td>{rate:.2f}</td></tr>"
        )
    lines.append("</table>")
    return "".join(lines)
```

- [ ] **Step 3: Smoke each renderer manually**

```bash
.venv/bin/python -c "
from personal_db.config import Config
from personal_db.templates.trackers.code_agent_activity import visualizations as v
cfg = Config(root='~/personal_db')
print(v.render_runtime_heatmap(cfg))
print(v.render_state_breakdown(cfg))
print(v.render_prompt_cadence(cfg))
print(v.render_engagement(cfg))
"
```

Expected: HTML strings printed for each — they may be near-empty if no data exists yet but they should not raise.

- [ ] **Step 4: Reinstall the tracker so the live copy includes visualizations.py**

```bash
.venv/bin/python -m personal_db --root ~/personal_db tracker reinstall code_agent_activity
```

Open the dashboard (`http://127.0.0.1:8765/t/code_agent_activity`) and confirm the four sections render.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/visualizations.py
git commit -m "feat(code-agent): visualizations

Four renderers: 7-day runtime heatmap, daily state-breakdown bars,
prompt-cadence histogram, engagement table joining mosspath_lite_events
(graceful fallback if that tracker isn't installed).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: End-to-end integration test

Mirror the existing `tests/integration/test_connector_mosspath_lite.py` pattern: install the tracker into a tmp root, drop fixture inputs, trigger sync via the daemon HTTP path, assert rows land.

**Files:**
- Create: `tests/integration/test_connector_code_agent_activity.py`

- [ ] **Step 1: Read the existing connector integration test for the pattern**

```bash
cat tests/integration/test_connector_mosspath_lite.py
```

Note how it:
1. Builds a Config rooted at tmp_path.
2. Calls `install_template` to land the tracker dir.
3. Drops fixture inputs (a fake mosspath SQLite).
4. Builds the daemon HTTP app and uses TestClient to POST to `/api/sync/{tracker}`.
5. Asserts row counts in the resulting `db.sqlite`.

- [ ] **Step 2: Write the test**

Create `tests/integration/test_connector_code_agent_activity.py`:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_db.config import Config
from personal_db.daemon.http import build_app
from personal_db.installer import install_template


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, TestClient]:
    cfg = Config(root=tmp_path / "personal_db")
    cfg.ensure_dirs()
    install_template("code_agent_activity", cfg)

    schema = (cfg.trackers_dir / "code_agent_activity" / "schema.sql").read_text()
    con = sqlite3.connect(cfg.db_path)
    con.executescript(schema)
    con.commit()
    con.close()

    # Hooks log: drop a synthetic two-session JSONL.
    hooks_log = cfg.state_dir / "code_agent_hooks.jsonl"
    hooks_log.parent.mkdir(parents=True, exist_ok=True)
    hooks_log.write_text(
        "\n".join(
            [
                json.dumps({"hook_event_name": "SessionStart", "session_id": "alpha", "received_at": "2026-05-09T10:00:00.000+00:00"}),
                json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "alpha", "received_at": "2026-05-09T10:00:05.000+00:00"}),
                json.dumps({"hook_event_name": "Stop", "session_id": "alpha", "received_at": "2026-05-09T10:00:30.000+00:00"}),
                json.dumps({"hook_event_name": "SessionEnd", "session_id": "alpha", "received_at": "2026-05-09T10:01:00.000+00:00"}),
                json.dumps({"hook_event_name": "SessionStart", "session_id": "beta", "received_at": "2026-05-09T10:00:10.000+00:00"}),
                json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "beta", "received_at": "2026-05-09T10:00:12.000+00:00"}),
                json.dumps({"hook_event_name": "Stop", "session_id": "beta", "received_at": "2026-05-09T10:00:40.000+00:00"}),
                "",
            ]
        )
    )
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(hooks_log))

    # Codex sessions: drop one minimal rollout file.
    codex_root = tmp_path / "codex_home" / "sessions" / "2026" / "05" / "09"
    codex_root.mkdir(parents=True)
    (codex_root / "rollout-test.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-05-09T11:00:00.000Z", "type": "session_meta", "payload": {"session_id": "codex-1", "cwd": "/p"}}),
                json.dumps({"timestamp": "2026-05-09T11:00:02.000Z", "type": "event_msg", "payload": {"type": "user_message", "content": "<r>"}}),
                json.dumps({"timestamp": "2026-05-09T11:00:30.000Z", "type": "event_msg", "payload": {"type": "task_complete"}}),
                "",
            ]
        )
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex_home"))

    app = build_app(cfg)
    return cfg, TestClient(app)


def test_sync_via_daemon_endpoint(env) -> None:
    cfg, client = env
    r = client.post("/api/sync/code_agent_activity")
    assert r.status_code == 200, r.text

    con = sqlite3.connect(cfg.db_path)
    sessions = con.execute(
        "SELECT agent, session_id, COUNT(*) FROM code_agent_events GROUP BY agent, session_id"
    ).fetchall()
    con.close()

    sessions_set = {(a, s) for a, s, _ in sessions}
    assert ("claude_code", "alpha") in sessions_set
    assert ("claude_code", "beta") in sessions_set
    assert ("codex_cli", "codex-1") in sessions_set


def test_concurrent_sessions_have_separate_intervals(env) -> None:
    cfg, client = env
    client.post("/api/sync/code_agent_activity")

    con = sqlite3.connect(cfg.db_path)
    alpha = con.execute(
        "SELECT COUNT(*) FROM code_agent_intervals WHERE agent='claude_code' AND session_id='alpha'"
    ).fetchone()[0]
    beta = con.execute(
        "SELECT COUNT(*) FROM code_agent_intervals WHERE agent='claude_code' AND session_id='beta'"
    ).fetchone()[0]
    con.close()

    assert alpha > 0
    assert beta > 0


def test_install_hooks_action_via_daemon(env, tmp_path: Path) -> None:
    cfg, client = env
    settings = tmp_path / "claude_settings.json"

    # Override the actions module's settings path to our tmp file.
    # The action handler reads cfg.claude_settings_path if set; daemon dispatch
    # passes cfg directly. Set it via attribute.
    cfg.claude_settings_path = settings  # type: ignore[attr-defined]

    r = client.post("/api/trackers/code_agent_activity/actions/install_hooks")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert settings.exists()
    body = json.loads(settings.read_text())
    assert "SessionStart" in body["hooks"]
```

> **Note for the implementer:** the third test assumes `Config` is mutable / accepts a `claude_settings_path` attribute. If `Config` is a frozen dataclass, instead use a monkeypatch:
> ```python
> from personal_db.templates.trackers.code_agent_activity import actions
> monkeypatch.setattr(actions, "_settings_path", lambda cfg: settings)
> ```

- [ ] **Step 3: Run the integration test**

Run: `.venv/bin/python -m pytest tests/integration/test_connector_code_agent_activity.py -v`
Expected: PASS — all three tests green.

- [ ] **Step 4: Run the full unit + integration suite**

Run:
```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_*.py tests/unit/test_daemon_actions_endpoint.py tests/integration/test_connector_code_agent_activity.py -v
```

Expected: PASS for all.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_connector_code_agent_activity.py
git commit -m "test(code-agent): integration test exercising daemon HTTP path

Two synthetic sessions in code_agent_hooks.jsonl plus one fixture Codex
rollout file -> sync via POST /api/sync/code_agent_activity -> assert rows
land in db.sqlite. Plus an action-endpoint test that exercises
install_hooks against a tmp settings.json.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Final smoke

After all 11 tasks are committed, run the end-to-end smoke against your real machine:

```bash
# 1. Reinstall the tracker into the live root.
.venv/bin/python -m personal_db --root ~/personal_db tracker reinstall code_agent_activity

# 2. Install the Claude Code hooks via the daemon (or via the wizard UI).
curl -s -X POST http://127.0.0.1:8765/api/trackers/code_agent_activity/actions/install_hooks | python3 -m json.tool

# 3. Verify they're wired up.
curl -s -X POST http://127.0.0.1:8765/api/trackers/code_agent_activity/actions/verify_hooks | python3 -m json.tool

# 4. Open a *fresh* Claude Code session in any project and send a prompt or two.
#    Then close it.

# 5. Confirm the hook writer logged events.
wc -l ~/personal_db/state/code_agent_hooks.jsonl

# 6. Sync and check the DB.
.venv/bin/python -m personal_db --root ~/personal_db sync code_agent_activity
sqlite3 ~/personal_db/db.sqlite "
  SELECT agent, COUNT(*) AS events
  FROM code_agent_events
  GROUP BY agent;
  SELECT agent, state, COUNT(*) AS intervals
  FROM code_agent_intervals
  GROUP BY agent, state;
"

# 7. Open the dashboard and look at the visualizations.
open "http://127.0.0.1:8765/t/code_agent_activity"
```

If any of those don't behave, the most likely failure modes are:

- **Step 5 produces 0 lines:** the hook command embedded in `~/.claude/settings.json` can't find `personal-db`. Re-run install; check `_resolve_hook_command` resolved correctly. Inspect `cat ~/.claude/settings.json` — the command field should be an absolute path.
- **Step 6 reports 0 codex_cli rows:** the rollout `payload.type` markers in your real files don't match the v1 map. Run the inspection from Task 3 Step 3 and adjust `_CODEX_PAYLOAD_MAP`.
