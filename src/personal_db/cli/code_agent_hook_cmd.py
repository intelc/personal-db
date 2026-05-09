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
    """Single os.write() call on an O_APPEND fd — seek-to-end and write are
    atomic per POSIX for concurrent writers on a regular file. Hook payload
    lines are short so we stay well within any filesystem's atomic-write
    boundary."""
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
        # SSH detection: if Claude Code was invoked over SSH, the hook process
        # inherits SSH_CONNECTION from its parent. Stamp this so engagement
        # views can flag remote sessions where mosspath-lite can't see the
        # user's keystrokes (they're happening on the client machine).
        payload["_is_remote"] = bool(os.environ.get("SSH_CONNECTION"))
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        _append_line(_default_log_path(), line)
    except Exception as exc:  # noqa: BLE001 — must never propagate
        print(f"code-agent-hook-write: {exc}", file=sys.stderr)
    # Always exit 0
