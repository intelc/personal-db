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
            text=True,
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
