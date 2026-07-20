"""Chrome native-messaging host for Personal DB's XHS collector.

The extension keeps Chrome-session collection separate from tracker code.  The
only local IPC surface is a user-only Unix socket.  Requests are deliberately
allowlisted here *and* in the extension so another local process cannot use the
bridge as an arbitrary browser automation endpoint.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import struct
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_TIMEOUT_MS = 660_000  # XHS collectors may legitimately run for 10 min.
# A historical saved-feed sync can send many known IDs for overlap detection.
# Keep a finite bound without rejecting an ordinary large personal collection.
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_NATIVE_MESSAGE_BYTES = 16 * 1024 * 1024

COLLECTORS: dict[str, dict[str, Any]] = {
    "collectors/xhs/creator.js": {
        "global": "__personalDbXhsCreator",
        "sources": {"xhs"},
        "hosts": {"creator.xiaohongshu.com"},
        "cfg": {"maxScrolls", "delayMs", "settleMs"},
    },
    "collectors/xhs/saved.js": {
        "global": "__personalDbXhsSaved",
        "sources": {"xhs_saved"},
        "hosts": {"www.xiaohongshu.com", "xiaohongshu.com"},
        "cfg": {
            "maxScrolls",
            "delayMs",
            "settleMs",
            "knownIds",
            "overlapStop",
            "deepBackfill",
        },
    },
}


class RequestError(ValueError):
    """A socket request was outside the narrow browser-collection contract."""


def _socket_path() -> Path:
    configured = os.environ.get("PDB_BROWSER_BRIDGE_SOCK")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "personal_db" / "state" / "browser-collector.sock"


def _is_json_value(value: Any) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def validate_request(request: Any) -> dict[str, Any]:
    """Return a sanitized request or raise for anything outside the allowlist."""
    if not isinstance(request, dict):
        raise RequestError("request must be an object")
    command = request.get("cmd")
    if command == "ping":
        if set(request) != {"cmd"}:
            raise RequestError("ping does not accept additional fields")
        return {"cmd": "ping"}
    if command != "collect" or set(request) != {"cmd", "job"}:
        raise RequestError("only ping and allowlisted collect requests are supported")

    job = request["job"]
    if not isinstance(job, dict):
        raise RequestError("collect job must be an object")
    allowed_job_fields = {
        "source", "url", "collectorFile", "globalName", "cfg", "timeoutMs"
    }
    if set(job) - allowed_job_fields:
        raise RequestError("collect job has unsupported fields")
    collector_file = job.get("collectorFile")
    spec = COLLECTORS.get(collector_file)
    if spec is None:
        raise RequestError("collectorFile is not allowlisted")
    if job.get("source") not in spec["sources"]:
        raise RequestError("source does not match collectorFile")
    if job.get("globalName") != spec["global"]:
        raise RequestError("globalName does not match collectorFile")
    url = job.get("url")
    parsed = urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme != "https" or parsed.hostname not in spec["hosts"]:
        raise RequestError("collection URL is not an allowlisted XHS URL")

    cfg = job.get("cfg", {})
    if not isinstance(cfg, dict) or set(cfg) - spec["cfg"] or not _is_json_value(cfg):
        raise RequestError("collector config is invalid or contains unsupported fields")
    for key in ("maxScrolls", "delayMs", "settleMs", "overlapStop"):
        if key in cfg and (
            not isinstance(cfg[key], int) or isinstance(cfg[key], bool) or cfg[key] < 0
        ):
            raise RequestError(f"{key} must be a non-negative integer")
    if "deepBackfill" in cfg and not isinstance(cfg["deepBackfill"], bool):
        raise RequestError("deepBackfill must be a boolean")
    if "knownIds" in cfg and (
        not isinstance(cfg["knownIds"], list)
        or len(cfg["knownIds"]) > 50_000
        or any(not isinstance(note_id, str) or len(note_id) > 128 for note_id in cfg["knownIds"])
    ):
        raise RequestError("knownIds must be a bounded list of strings")
    timeout_ms = job.get("timeoutMs", 600_000)
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= 600_000:
        raise RequestError("timeoutMs must be between 1 and 600000")
    return {
        "cmd": "collect",
        "job": {
            "source": job["source"],
            "url": url,
            "collectorFile": collector_file,
            "globalName": spec["global"],
            "cfg": cfg,
            "timeoutMs": timeout_ms,
        },
    }


def _read_exact(stream: Any, length: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@dataclass
class PendingReply:
    event: threading.Event
    reply: dict[str, Any] | None = None


class NativeBridge:
    """Thread-safe native-message framing and request/reply correlation."""

    def __init__(self, timeout_ms: int) -> None:
        self.timeout_ms = timeout_ms
        self._next_id = 1
        self._pending: dict[int, PendingReply] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.closed = threading.Event()

    def send(self, message: dict[str, Any]) -> None:
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_NATIVE_MESSAGE_BYTES:
            raise RuntimeError("native message exceeds size limit")
        with self._write_lock:
            sys.stdout.buffer.write(struct.pack("<I", len(body)) + body)
            sys.stdout.buffer.flush()

    def ask(self, request: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            pending = PendingReply(threading.Event())
            self._pending[request_id] = pending
        try:
            self.send({"id": request_id, **request})
            if not pending.event.wait(self.timeout_ms / 1000):
                raise TimeoutError("extension response timed out")
            if pending.reply is None:
                raise RuntimeError("extension bridge closed")
            return pending.reply
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def read_forever(self) -> None:
        try:
            while True:
                header = _read_exact(sys.stdin.buffer, 4)
                if header is None:
                    break
                length = struct.unpack("<I", header)[0]
                if length > MAX_NATIVE_MESSAGE_BYTES:
                    raise RuntimeError("native message exceeds size limit")
                body = _read_exact(sys.stdin.buffer, length)
                if body is None:
                    break
                try:
                    message = json.loads(body)
                except json.JSONDecodeError:
                    continue
                request_id = message.get("id") if isinstance(message, dict) else None
                if not isinstance(request_id, int):
                    continue
                with self._lock:
                    pending = self._pending.get(request_id)
                if pending:
                    pending.reply = message
                    pending.event.set()
        finally:
            self.closed.set()
            with self._lock:
                values = list(self._pending.values())
            for pending in values:
                pending.event.set()


class UnixRequestServer:
    def __init__(self, path: Path, bridge: NativeBridge) -> None:
        self.path = path
        self.bridge = bridge
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # The pathname is global while native hosts can briefly overlap during
        # an extension reload. Remember the inode we actually bound so an old
        # host never unlinks a replacement host's freshly bound socket.
        self._bound_identity: tuple[int, int] | None = None

    def bind(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        if self.path.exists() or self.path.is_symlink():
            mode = self.path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise RuntimeError(f"refusing to replace non-socket path: {self.path}")
            self.path.unlink()
        old_umask = os.umask(0o077)
        try:
            self.socket.bind(str(self.path))
        finally:
            os.umask(old_umask)
        self.path.chmod(0o600)
        bound = self.path.lstat()
        if not stat.S_ISSOCK(bound.st_mode):
            raise RuntimeError(f"bound path is not a socket: {self.path}")
        self._bound_identity = (bound.st_dev, bound.st_ino)
        self.socket.listen(8)
        self.socket.settimeout(1.0)

    def serve(self) -> None:
        while not self.bridge.closed.is_set():
            try:
                conn, _ = self.socket.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(10)
            raw = bytearray()
            try:
                while len(raw) <= MAX_REQUEST_BYTES:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if b"\n" in chunk:
                        break
                line = bytes(raw).split(b"\n", 1)[0]
                if not line or len(line) > MAX_REQUEST_BYTES:
                    raise RequestError("request is empty or too large")
                request = validate_request(json.loads(line))
                reply = self.bridge.ask(request)
                if reply.get("error"):
                    response = {"ok": False, "error": str(reply["error"])}
                else:
                    response = {"ok": True, "result": reply.get("result")}
            except (OSError, ValueError, TimeoutError, RuntimeError) as exc:
                response = {"ok": False, "error": str(exc)}
            with suppress(OSError):
                conn.sendall(json.dumps(response, separators=(",", ":")).encode() + b"\n")

    def close(self) -> None:
        try:
            self.socket.close()
        finally:
            try:
                current = self.path.lstat()
                current_identity = (current.st_dev, current.st_ino)
                if (
                    self._bound_identity == current_identity
                    and stat.S_ISSOCK(current.st_mode)
                ):
                    self.path.unlink()
            except OSError:
                pass


def main() -> None:
    raw_timeout = os.environ.get("PDB_BROWSER_BRIDGE_TIMEOUT_MS", str(DEFAULT_TIMEOUT_MS))
    try:
        timeout_ms = max(DEFAULT_TIMEOUT_MS, int(raw_timeout))
    except ValueError:
        timeout_ms = DEFAULT_TIMEOUT_MS
    bridge = NativeBridge(timeout_ms)
    server = UnixRequestServer(_socket_path(), bridge)
    server.bind()
    threading.Thread(target=bridge.read_forever, daemon=True).start()
    bridge.send({"hello": "personal-db-xhs-collector", "pid": os.getpid()})
    try:
        server.serve()
    finally:
        server.close()


if __name__ == "__main__":
    main()
