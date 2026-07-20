from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from personal_db.core.browser_bridge import (
    BrowserBridgeUnavailable,
    browser_bridge_request,
    browser_bridge_socket_path,
    browser_collect,
)


def _serve_one_unix_reply(socket_path: Path, reply: dict, seen: list[dict]) -> threading.Thread:
    """Start a one-request fake native host and return its worker thread."""

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)

    def serve() -> None:
        try:
            conn, _ = server.accept()
            with conn:
                raw = b""
                while b"\n" not in raw:
                    raw += conn.recv(4096)
                seen.append(json.loads(raw.split(b"\n", 1)[0]))
                conn.sendall(json.dumps(reply).encode() + b"\n")
        finally:
            server.close()
            socket_path.unlink(missing_ok=True)

    thread = threading.Thread(target=serve)
    thread.start()
    return thread


def _short_socket_path() -> Path:
    """macOS AF_UNIX paths are much shorter than pytest's tmp paths."""

    return Path("/tmp") / f"pdb-{uuid4().hex}.sock"


def test_browser_bridge_request_uses_newline_json_contract(tmp_path):
    socket_path = _short_socket_path()
    seen: list[dict] = []
    thread = _serve_one_unix_reply(socket_path, {"ok": True, "result": {"pong": True}}, seen)

    assert browser_bridge_request({"cmd": "ping"}, socket_path=socket_path) == {"pong": True}
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert seen == [{"cmd": "ping"}]


def test_browser_collect_wraps_job_in_extension_contract(tmp_path):
    socket_path = _short_socket_path()
    seen: list[dict] = []
    reply = {"ok": True, "result": {"source": "xhs", "data": {"rows": [{"note_id": "n1"}]}}}
    thread = _serve_one_unix_reply(socket_path, reply, seen)

    result = browser_collect(
        {
            "source": "xhs",
            "url": "https://creator.xiaohongshu.com/new/note-manager",
            "collectorFile": "collectors/xhs/creator.js",
            "globalName": "__personalDbXhsCreator",
            "timeoutMs": 1_000,
        },
        socket_path=socket_path,
    )

    thread.join(timeout=1)
    assert result["data"]["rows"] == [{"note_id": "n1"}]
    assert seen[0]["cmd"] == "collect"
    assert seen[0]["job"]["globalName"] == "__personalDbXhsCreator"


def test_browser_bridge_default_socket_tracks_personal_db_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("PDB_BROWSER_BRIDGE_SOCK", raising=False)
    assert browser_bridge_socket_path(tmp_path) == tmp_path / "browser-collector.sock"

    override = tmp_path / "elsewhere.sock"
    monkeypatch.setenv("PDB_BROWSER_BRIDGE_SOCK", str(override))
    assert browser_bridge_socket_path(tmp_path) == override


def test_browser_bridge_unavailable_has_extension_guidance(tmp_path):
    with pytest.raises(BrowserBridgeUnavailable, match="XHS Collector extension"):
        browser_bridge_request(
            {"cmd": "ping"}, socket_path=tmp_path / "missing.sock", timeout_s=0.1
        )
