"""Client for the optional Personal DB Chrome collector extension.

The extension owns browser windows and authenticated page collection.  This
small client deliberately only speaks a local Unix-socket protocol, allowing
trackers to keep their parsing and persistence code independent from Chrome.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_SOCKET_NAME = "browser-collector.sock"
DEFAULT_TIMEOUT_S = 300.0
MAX_REPLY_BYTES = 16 * 1024 * 1024


class BrowserBridgeError(RuntimeError):
    """A bridge request was understood but could not complete."""


class BrowserBridgeUnavailable(BrowserBridgeError):
    """The local extension/native-host socket cannot be reached."""


class BrowserBridgeProtocolError(BrowserBridgeError):
    """The native host returned an invalid reply."""


def browser_bridge_socket_path(state_dir: Path | None = None) -> Path:
    """Return the bridge socket path, respecting the explicit environment override.

    A Personal DB root can be supplied with ``--root``.  Deriving the default
    from its state directory prevents a custom root from accidentally talking
    to another Personal DB installation's extension host.
    """

    override = (os.environ.get("PDB_BROWSER_BRIDGE_SOCK") or "").strip()
    if override:
        return Path(override).expanduser()
    if state_dir is not None:
        return Path(state_dir) / DEFAULT_SOCKET_NAME
    return Path("~/personal_db/state").expanduser() / DEFAULT_SOCKET_NAME


def browser_bridge_request(
    request: Mapping[str, Any],
    *,
    state_dir: Path | None = None,
    socket_path: Path | str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Any:
    """Send one newline-delimited JSON request and return its ``result``.

    The native host uses ``{ok, result}`` / ``{ok: false, error}`` envelopes.
    Connection failures are distinguished from collector failures so callers
    can give users an actionable extension-installation message.
    """

    path = (
        Path(socket_path).expanduser()
        if socket_path is not None
        else browser_bridge_socket_path(state_dir)
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout_s)
            conn.connect(str(path))
            encoded = json.dumps(dict(request), separators=(",", ":")).encode("utf-8") + b"\n"
            conn.sendall(encoded)
            chunks: list[bytes] = []
            received = 0
            while True:
                chunk = conn.recv(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > MAX_REPLY_BYTES:
                    raise BrowserBridgeProtocolError(
                        f"Personal DB browser bridge reply exceeded {MAX_REPLY_BYTES // (1024 * 1024)} MiB"
                    )
                if b"\n" in chunk:
                    break
    except BrowserBridgeError:
        raise
    except (TimeoutError, FileNotFoundError, ConnectionRefusedError, OSError) as exc:
        raise BrowserBridgeUnavailable(
            f"Personal DB browser bridge is unavailable at {path}: {exc}. "
            "Open Chrome with the Personal DB XHS Collector extension enabled, then retry."
        ) from exc

    raw = b"".join(chunks).split(b"\n", 1)[0]
    if not raw:
        raise BrowserBridgeProtocolError("Personal DB browser bridge closed without a reply")
    try:
        reply = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrowserBridgeProtocolError(
            "Personal DB browser bridge returned invalid JSON"
        ) from exc
    if not isinstance(reply, dict):
        raise BrowserBridgeProtocolError("Personal DB browser bridge returned a non-object reply")
    if reply.get("ok") is False or reply.get("error"):
        raise BrowserBridgeError(
            str(reply.get("error") or "Personal DB browser bridge request failed")
        )
    if "result" not in reply:
        raise BrowserBridgeProtocolError("Personal DB browser bridge reply is missing result")
    return reply["result"]


def browser_collect(
    job: Mapping[str, Any],
    *,
    state_dir: Path | None = None,
    socket_path: Path | str | None = None,
) -> dict[str, Any]:
    """Run one extension collector job and return its result envelope."""

    timeout_ms = job.get("timeoutMs")
    try:
        timeout_s = float(timeout_ms) / 1000 + 30 if timeout_ms else DEFAULT_TIMEOUT_S
    except (TypeError, ValueError):
        timeout_s = DEFAULT_TIMEOUT_S
    result = browser_bridge_request(
        {"cmd": "collect", "job": dict(job)},
        state_dir=state_dir,
        socket_path=socket_path,
        timeout_s=timeout_s,
    )
    if not isinstance(result, dict):
        raise BrowserBridgeProtocolError(
            "Personal DB browser collector returned a non-object result"
        )
    return result
