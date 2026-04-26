from __future__ import annotations

import http.server
import json
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from personal_db.config import Config


class OAuthFlow:
    """Local HTTP server that captures the OAuth callback ?code=…&state=…."""

    def __init__(self, state: str, port: int = 0):
        self._state_param = state
        self._code: str | None = None
        self._event = threading.Event()

        flow = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                qs = urllib.parse.urlparse(self.path).query
                params = dict(urllib.parse.parse_qsl(qs))
                if params.get("state") != flow._state_param:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"state mismatch")
                    return
                flow._code = params.get("code")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"OK. You may close this tab.")
                flow._event.set()

            def log_message(self, *_: Any) -> None:
                pass

        self._server = socketserver.TCPServer(("127.0.0.1", port), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wait_for_code(self, timeout_s: float = 120) -> str | None:
        self._event.wait(timeout=timeout_s)
        return self._code

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _token_path(cfg: Config, provider: str) -> Path:
    d = cfg.state_dir / "oauth"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{provider}.json"


def save_token(cfg: Config, provider: str, token: dict[str, Any]) -> None:
    p = _token_path(cfg, provider)
    p.write_text(json.dumps(token))
    p.chmod(0o600)


def load_token(cfg: Config, provider: str) -> dict[str, Any] | None:
    p = _token_path(cfg, provider)
    return json.loads(p.read_text()) if p.exists() else None


def refresh_if_needed(
    cfg: Config,
    provider: str,
    token_url: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Refresh the token if expired. Returns the (possibly refreshed) token."""
    token = load_token(cfg, provider) or {}
    if token.get("expires_at", 0) > time.time() + 60:
        return token
    if "refresh_token" not in token:
        raise RuntimeError(f"{provider}: no refresh_token; re-run setup")
    r = requests.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=10,
    )
    r.raise_for_status()
    new_token = r.json()
    new_token["expires_at"] = int(time.time()) + int(new_token.get("expires_in", 3600))
    if "refresh_token" not in new_token:
        new_token["refresh_token"] = token["refresh_token"]
    save_token(cfg, provider, new_token)
    return new_token


def exchange_code(
    *,
    token_url: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Exchange an OAuth authorization code for an access token.

    Counterpart to refresh_if_needed for the initial code-for-token step.
    """
    r = requests.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=10,
    )
    r.raise_for_status()
    token = r.json()
    token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
    return token
