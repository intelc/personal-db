from __future__ import annotations

import errno
import http.server
import importlib.util
import json
import secrets
import socketserver
import ssl
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

import requests

from personal_db.core.config import Config


def _get_ssl_context(state_dir: Path) -> ssl.SSLContext:
    """Self-signed cert+key for `https://localhost` OAuth callbacks.

    Some providers (Instagram Login) require an HTTPS redirect URI even
    for localhost. We generate a 10-year self-signed cert under
    state_dir/oauth/.ssl/ on first use and reuse it on every subsequent
    OAuth flow. The user clicks through the browser's cert warning once
    per browser; the callback itself only does a 302 redirect back to
    the daemon, so the warning is brief.
    """
    ssl_dir = state_dir / "oauth" / ".ssl"
    ssl_dir.mkdir(parents=True, exist_ok=True)
    cert = ssl_dir / "localhost.crt"
    key = ssl_dir / "localhost.key"
    if not cert.exists() or not key.exists():
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(cert),
                "-days", "3650", "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
        )
        cert.chmod(0o600)
        key.chmod(0o600)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return ctx


class OAuthFlow:
    """Local HTTP(S) server that captures the OAuth callback ?code=…&state=….

    Pass `scheme="https"` (and a state_dir) when the provider requires an
    HTTPS redirect URI (e.g. Instagram Login). The server then wraps its
    socket with a self-signed cert auto-generated under state_dir.
    """

    def __init__(
        self,
        state: str,
        port: int = 0,
        *,
        scheme: str = "http",
        state_dir: Path | None = None,
    ):
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
        if scheme == "https":
            if state_dir is None:
                raise ValueError("state_dir is required when scheme='https'")
            ctx = _get_ssl_context(state_dir)
            self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)
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
      access_token, expires_in
    plus refresh_token when the provider issues one. On `refresh_token` calls
    where the provider omits a new refresh_token, the dispatcher (refresh_if_needed)
    carries the prior refresh_token forward — adapters do not need to do this.
    The dispatcher also adds expires_at.
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
_STANDARD_ADAPTER: "TokenAdapter" = StandardAdapter()


def register_adapter(provider: str, adapter: TokenAdapter) -> None:
    """Register a TokenAdapter for `provider`. Idempotent: re-registering replaces."""
    _adapters[provider] = adapter


def _adapter_for(provider: str) -> TokenAdapter:
    return _adapters.get(provider, _STANDARD_ADAPTER)


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
    """Refresh the token if expired. Returns the (possibly refreshed) token.

    Dispatches the actual refresh wire call through `_adapter_for(provider)`
    so providers with non-standard token endpoints (e.g. Withings) can
    override the request shape and response parsing.

    If the provider omits a new `refresh_token` in its response, the prior
    refresh_token is carried forward (this stays in the dispatcher rather
    than the adapter — see TokenAdapter docstring).
    """
    token = load_token(cfg, provider) or {}
    if token.get("expires_at", 0) > time.time() + 60:
        return token
    if "refresh_token" not in token:
        raise RuntimeError(f"{provider}: no refresh_token; re-run setup")
    adapter = _adapter_for(provider)
    new_token = adapter.refresh_token(
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=token["refresh_token"],
    )
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
    provider: str = "_standard",
) -> dict[str, Any]:
    """Exchange an OAuth authorization code for an access token.

    Dispatches through `_adapter_for(provider)` so providers with non-standard
    token endpoints (e.g. Withings) can override the wire format. The default
    `_standard` provider routes to StandardAdapter, preserving prior behavior.
    """
    adapter = _adapter_for(provider)
    token = adapter.exchange_code(
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        redirect_uri=redirect_uri,
    )
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
    scheme: str = "http",
    scope_separator: str = " ",
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
    redirect_uri = f"{scheme}://{redirect_host}:{redirect_port}{redirect_path}"
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
                    provider=provider,
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

    try:
        server = _ReusableTCPServer((redirect_host, redirect_port), _Handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            friendly = OSError(
                f"Port {redirect_port} is in use — if you just ran another "
                "tracker's authorization, wait for it to finish (or ~10 "
                "minutes for it to expire) and try again."
            )
            friendly.errno = errno.EADDRINUSE
            raise friendly from e
        raise
    if scheme == "https":
        ctx = _get_ssl_context(cfg.state_dir)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
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
        auth_params["scope"] = scope_separator.join(scopes)
    return auth_url + "?" + urllib.parse.urlencode(auth_params)


def ensure_adapter_from_manifest(tracker_dir: Path, step: Any) -> None:
    """Load `<tracker_dir>/<module>.py` and register `<class>()` for `step.provider`.

    No-op if `step.adapter` is None or the provider is already registered with
    the same class. Idempotent: safe to call repeatedly.

    `step` is typed as Any to avoid a circular import on `OAuthStep`; only
    `step.adapter` and `step.provider` attributes are accessed.
    """
    spec_str = getattr(step, "adapter", None)
    if not spec_str:
        return
    if ":" not in spec_str:
        raise RuntimeError(
            f"Invalid adapter spec {spec_str!r}: expected '<module>:<class>'"
        )
    module_name, _, class_name = spec_str.partition(":")
    provider = step.provider
    existing = _adapters.get(provider)
    if existing is not None and existing.__class__.__name__ == class_name:
        return
    module_path = tracker_dir / f"{module_name}.py"
    if not module_path.exists():
        raise RuntimeError(
            f"OAuth adapter module not found: {module_path} "
            f"(declared as {spec_str} in manifest)"
        )
    spec = importlib.util.spec_from_file_location(
        f"personal_db_oauth_adapter_{provider}_{module_name}",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Could not load OAuth adapter module from {module_path}"
        )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, class_name, None)
    if cls is None:
        raise RuntimeError(
            f"OAuth adapter class {class_name} not found in {module_path}"
        )
    register_adapter(provider, cls())
