from __future__ import annotations

import http.server
import json
import secrets
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

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


class TokenAdapter(Protocol):
    """Provider-specific override for OAuth token exchange/refresh.

    Implementations return a token dict containing at least:
      access_token, refresh_token, expires_in
    The dispatcher (refresh_if_needed / exchange_code) adds expires_at.
    """

    def exchange_code(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]: ...

    def refresh_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]: ...


class StandardAdapter:
    """Default RFC 6749 token flow used when no per-provider adapter is registered."""

    def exchange_code(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
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
        return r.json()

    def refresh_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]:
        r = requests.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()


_adapters: dict[str, TokenAdapter] = {}


def register_adapter(provider: str, adapter: TokenAdapter) -> None:
    """Register a TokenAdapter for `provider`. Idempotent: re-registering replaces."""
    _adapters[provider] = adapter


def _adapter_for(provider: str) -> TokenAdapter:
    return _adapters.get(provider) or StandardAdapter()


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


# --- web OAuth flow --------------------------------------------------------
#
# Used by the dashboard's `/setup/oauth/<name>` route. Spawns a one-shot
# localhost callback server on the manifest's `redirect_port` (the URI the
# user pre-registered with the OAuth provider), exchanges the code for a
# token in the handler itself, then 302-redirects the user's browser back to
# the daemon. Single-process, single in-flight session per provider — old
# sessions are shut down before a new one is started, and a watchdog reaps
# abandoned sessions after `timeout_s`.

_active_web_sessions: dict[str, "_WebOAuthSession"] = {}
_active_web_sessions_lock = threading.Lock()


class _ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


class _WebOAuthSession:
    def __init__(self, server: _ReusableTCPServer, thread: threading.Thread):
        self.server = server
        self.thread = thread

    def shutdown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


def _shutdown_existing(provider: str) -> None:
    with _active_web_sessions_lock:
        prior = _active_web_sessions.pop(provider, None)
    if prior is not None:
        prior.shutdown()


def start_web_oauth(
    cfg: Config,
    *,
    provider: str,
    auth_url: str,
    token_url: str,
    client_id: str,
    client_secret: str,
    redirect_host: str,
    redirect_port: int,
    redirect_path: str,
    scopes: list[str],
    success_redirect: str,
    failure_redirect: str | None = None,
    timeout_s: float = 600,
) -> str:
    """Spawn a one-shot HTTP server on (redirect_host, redirect_port). On
    callback, validate state, exchange the code, persist the token via
    save_token(cfg, provider, ...), and 302-redirect to `success_redirect`
    (or `failure_redirect` with an `oauth_error=` query param on failure).

    Returns the provider's authorization URL — the caller should redirect the
    user's browser there. The local server self-shuts after the callback
    fires, or after `timeout_s` seconds if the user abandons the flow.
    """
    _shutdown_existing(provider)

    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://{redirect_host}:{redirect_port}{redirect_path}"
    failure_redirect = failure_redirect or success_redirect

    def _with_error(target: str, err: str) -> str:
        sep = "&" if "?" in target else "?"
        return f"{target}{sep}oauth_error={urllib.parse.quote(err[:200])}"

    server_holder: dict[str, _ReusableTCPServer] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server convention
            qs = urllib.parse.urlparse(self.path).query
            params = dict(urllib.parse.parse_qsl(qs))

            err = params.get("error")
            if err:
                self._redirect(_with_error(failure_redirect, err))
                return
            if params.get("state") != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"state mismatch")
                return
            code = params.get("code")
            if not code:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"missing code")
                return
            try:
                token = exchange_code(
                    token_url=token_url,
                    client_id=client_id,
                    client_secret=client_secret,
                    code=code,
                    redirect_uri=redirect_uri,
                )
                save_token(cfg, provider, token)
            except Exception as e:  # noqa: BLE001 — funnel into UI
                self._redirect(_with_error(failure_redirect, str(e)))
                return
            self._redirect(success_redirect)

        def _redirect(self, url: str) -> None:
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()
            srv = server_holder.get("s")
            if srv is not None:
                threading.Thread(target=srv.shutdown, daemon=True).start()

        def log_message(self, *_: Any) -> None:
            pass

    server = _ReusableTCPServer((redirect_host, redirect_port), _Handler)
    server_holder["s"] = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    session = _WebOAuthSession(server, thread)
    with _active_web_sessions_lock:
        _active_web_sessions[provider] = session

    def _watchdog() -> None:
        time.sleep(timeout_s)
        with _active_web_sessions_lock:
            current = _active_web_sessions.get(provider)
            if current is session:
                _active_web_sessions.pop(provider, None)
                session.shutdown()

    threading.Thread(target=_watchdog, daemon=True).start()

    auth_params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if scopes:
        auth_params["scope"] = " ".join(scopes)
    return auth_url + "?" + urllib.parse.urlencode(auth_params)
