"""HTTP client for the personal-db daemon. Used by CLI sync_cmd and MCP tools."""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from personal_db.core.config import DEFAULT_ROOT, Config
from personal_db.core.daemon_token import read_token

DEFAULT_URL = "http://127.0.0.1:8765"
_TIMEOUT_SECONDS = 300  # generous; sync can take minutes for large backfills


class DaemonUnreachable(RuntimeError):
    """Raised when the daemon is not accepting connections.

    Callers should translate this into a directive user-facing message:
    `personal-db daemon not running. Run \\`personal-db daemon install\\``.
    """


class DaemonError(RuntimeError):
    """Raised when the daemon responds with a 5xx or otherwise-malformed reply."""


def base_url() -> str:
    return os.environ.get("PERSONAL_DB_DAEMON_URL", DEFAULT_URL)


def _root() -> Path:
    """Resolve the data root the same way `cli.state.get_root()` does, minus
    the `--root` flag (client.py sits in services, which may not import
    cli). PERSONAL_DB_ROOT covers every real caller: the CLI's global
    callback sets it implicitly via --root -> env is not required there since
    the CLI process already knows its root -- but daemon.client is also used
    by the MCP server and other out-of-process callers, so falling back to
    the same env var + default keeps behavior consistent everywhere.
    """
    env_root = os.environ.get("PERSONAL_DB_ROOT")
    return Path(env_root).expanduser() if env_root else Path(DEFAULT_ROOT).expanduser()


def _auth_headers() -> dict[str, str]:
    token = read_token(Config(root=_root()))
    return {"Authorization": f"Bearer {token}"} if token else {}


def _post(path: str, params: dict | None = None) -> dict[str, Any]:
    url = f"{base_url()}{path}"
    try:
        resp = requests.post(
            url, params=params or {}, headers=_auth_headers(), timeout=_TIMEOUT_SECONDS
        )
    except (requests.ConnectionError, requests.Timeout) as e:
        raise DaemonUnreachable(
            f"daemon not running at {base_url()}: {e}"
        ) from e
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise DaemonError(f"daemon error: {resp.status_code} {resp.text[:200]}") from e
    return resp.json()


def _get(path: str) -> dict[str, Any]:
    url = f"{base_url()}{path}"
    try:
        resp = requests.get(url, headers=_auth_headers(), timeout=10)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise DaemonUnreachable(
            f"daemon not running at {base_url()}: {e}"
        ) from e
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise DaemonError(f"daemon error: {resp.status_code} {resp.text[:200]}") from e
    return resp.json()


def sync_one(name: str) -> dict[str, Any]:
    return _post(f"/api/v1/sync/{name}")


def sync_due() -> dict[str, Any]:
    return _post("/api/v1/sync_due")


def backfill(name: str, start: str | None, end: str | None) -> dict[str, Any]:
    params = {}
    if start:
        params["from"] = start
    if end:
        params["to"] = end
    return _post(f"/api/v1/backfill/{name}", params=params)


def health() -> dict[str, Any]:
    return _get("/api/v1/health")


def bootstrap_url(cfg: Config, *, base: str, path: str = "/") -> str:
    """Build a URL that authenticates a freshly opened browser tab and lands
    it on `path`, for launchers that hold the token file (CLI `ui`, menubar,
    the setup wizard) and want `webbrowser.open(...)` to just work.

    Mints a one-time code via the already-running daemon at `base` (see
    routes/auth.py) so the token itself never appears in the URL. Falls back
    to the plain `/auth` page — where the user can paste the token by hand —
    if minting fails for any reason (daemon not up yet, token unreadable,
    network hiccup); a launcher should never crash just because the
    convenience path didn't work.
    """
    quoted_path = urllib.parse.quote(path)
    token = read_token(cfg)
    if token:
        try:
            resp = requests.post(
                f"{base}/api/v1/auth/otc",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
            resp.raise_for_status()
            otc = resp.json()["otc"]
            return f"{base}/auth/bootstrap?otc={urllib.parse.quote(otc)}&next={quoted_path}"
        except (requests.RequestException, KeyError, ValueError, TypeError):
            pass
    return f"{base}/auth?next={quoted_path}"
