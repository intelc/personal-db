"""PTY-backed Claude/Codex terminal sessions for the dashboard drawer."""

from __future__ import annotations

import contextlib
import json
import os
import pty
import shlex
import signal
import struct
import subprocess
import termios
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from personal_db.core.config import Config

_MAX_BACKLOG_CHARS = 200_000
_MAX_CONTEXT_CHARS = 24_000


def _now() -> float:
    return time.time()


def _terminal_size(cols: int, rows: int) -> bytes:
    return struct.pack("HHHH", max(1, rows), max(2, cols), 0, 0)


def _coerce_size(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _daemon_path() -> str:
    existing = os.environ.get("PATH") or ""
    candidates = [
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".npm-global" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    parts: list[str] = []
    for item in [*candidates, *existing.split(os.pathsep)]:
        if item and item not in parts:
            parts.append(item)
    return os.pathsep.join(parts)


def build_context_prompt(context: dict[str, Any] | None) -> str:
    payload = json.dumps(context or {}, indent=2, sort_keys=True, default=str)
    if len(payload) > _MAX_CONTEXT_CHARS:
        payload = payload[:_MAX_CONTEXT_CHARS] + "\n...<truncated>"
    return (
        "You are running inside the personal_db dashboard terminal drawer.\n"
        "The user is looking at a local personal data UI. Use the page context below "
        "to orient yourself, and prefer querying personal_db through its CLI, MCP "
        "tools, or local HTTP API instead of guessing from the rendered page.\n\n"
        "Current page context JSON:\n"
        f"{payload}\n\n"
        "Start by giving a short acknowledgement of what page/data surface you can see, "
        "then wait for the user's instruction."
    )


def build_cli_command(cli_type: str, prompt: str) -> str:
    if cli_type == "codex":
        base = os.environ.get("PERSONAL_DB_CODEX_COMMAND", "codex")
        return f"{base} --no-alt-screen --dangerously-bypass-approvals-and-sandbox {shlex.quote(prompt)}"
    base = os.environ.get("PERSONAL_DB_CLAUDE_COMMAND", "claude")
    return f"{base} --permission-mode auto {shlex.quote(prompt)}"


@dataclass
class AgentTerminalSession:
    id: str
    cli_type: str
    command: str
    cwd: Path
    master_fd: int
    process: subprocess.Popen[bytes]
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    exit_code: int | None = None
    _backlog: deque[str] = field(default_factory=deque)
    _backlog_chars: int = 0
    _subscribers: list[tuple[Any, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _closed: bool = False

    def start_reader(self) -> None:
        thread = threading.Thread(target=self._read_loop, name=f"pdb-agent-{self.id}", daemon=True)
        thread.start()

    @property
    def alive(self) -> bool:
        return self.exit_code is None and self.process.poll() is None

    def _append_backlog(self, text: str) -> None:
        self._backlog.append(text)
        self._backlog_chars += len(text)
        while self._backlog_chars > _MAX_BACKLOG_CHARS and self._backlog:
            self._backlog_chars -= len(self._backlog.popleft())

    def _broadcast(self, message: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(queue.put_nowait, message)

    def _read_loop(self) -> None:
        try:
            while True:
                try:
                    data = os.read(self.master_fd, 8192)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                self.updated_at = _now()
                with self._lock:
                    self._append_backlog(text)
                self._broadcast({"type": "output", "data": text})
        finally:
            code = self.process.poll()
            if code is None:
                try:
                    code = self.process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    code = None
            self.exit_code = code
            self.updated_at = _now()
            self._broadcast({"type": "exit", "code": code})
            self.close_fd()

    def backlog(self) -> str:
        with self._lock:
            return "".join(self._backlog)

    def subscribe(self, loop: Any, queue: Any) -> None:
        with self._lock:
            self._subscribers.append((loop, queue))

    def unsubscribe(self, queue: Any) -> None:
        with self._lock:
            self._subscribers = [(loop, q) for loop, q in self._subscribers if q is not queue]

    def write(self, data: str) -> None:
        if not self.alive:
            return
        os.write(self.master_fd, data.encode("utf-8", errors="replace"))
        self.updated_at = _now()

    def resize(self, cols: int, rows: int) -> None:
        if self._closed:
            return
        import fcntl

        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, _terminal_size(cols, rows))
        except OSError:
            return

    def terminate(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except OSError:
            self.process.terminate()

    def close_fd(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.close(self.master_fd)
        except OSError:
            pass


class AgentTerminalManager:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._sessions: dict[str, AgentTerminalSession] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        cli_type: str,
        context: dict[str, Any] | None,
        cols: int = 100,
        rows: int = 30,
    ) -> AgentTerminalSession:
        cli = "codex" if cli_type == "codex" else "claude"
        prompt = build_context_prompt(context)
        command = build_cli_command(cli, prompt)
        master_fd, slave_fd = pty.openpty()
        size = _terminal_size(cols, rows)
        import fcntl

        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, size)
        shell = os.environ.get("SHELL") or "/bin/zsh"
        env = os.environ.copy()
        env["PATH"] = _daemon_path()
        env["TERM"] = env.get("TERM") or "xterm-256color"
        env["PERSONAL_DB_ROOT"] = str(self.cfg.root)
        env["PERSONAL_DB_DAEMON_URL"] = "http://127.0.0.1:8765"
        cwd = self.cfg.root
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.Popen(
                [shell, "-lc", command],
                cwd=str(cwd),
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)

        session = AgentTerminalSession(
            id=uuid.uuid4().hex[:12],
            cli_type=cli,
            command=command,
            cwd=cwd,
            master_fd=master_fd,
            process=process,
        )
        with self._lock:
            self._sessions[session.id] = session
        session.start_reader()
        return session

    def get(self, session_id: str) -> AgentTerminalSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [
            {
                "id": s.id,
                "cli_type": s.cli_type,
                "alive": s.alive,
                "exit_code": s.exit_code,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "cwd": str(s.cwd),
            }
            for s in sessions
        ]

    def terminate(self, session_id: str) -> bool:
        session = self.get(session_id)
        if not session:
            return False
        session.terminate()
        return True


async def attach_terminal_websocket(websocket: WebSocket, session: AgentTerminalSession) -> None:
    import asyncio

    await websocket.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    session.subscribe(loop, queue)
    backlog = session.backlog()
    if backlog:
        await websocket.send_json({"type": "output", "data": backlog})
    if not session.alive:
        await websocket.send_json({"type": "exit", "code": session.exit_code})

    async def sender() -> None:
        while True:
            message = await queue.get()
            await websocket.send_json(message)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            msg_type = message.get("type")
            if msg_type == "input":
                session.write(str(message.get("data") or ""))
            elif msg_type == "resize":
                cols = _coerce_size(message.get("cols"), 100, 2, 400)
                rows = _coerce_size(message.get("rows"), 30, 1, 200)
                session.resize(cols, rows)
            elif msg_type == "terminate":
                session.terminate()
    finally:
        session.unsubscribe(queue)
        sender_task.cancel()
        with contextlib.suppress(BaseException):
            await sender_task
