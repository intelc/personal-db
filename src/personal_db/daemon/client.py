"""HTTP client for the personal-db daemon. Used by CLI sync_cmd and MCP tools."""

from __future__ import annotations

import os
from typing import Any

import requests

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


def _post(path: str, params: dict | None = None) -> dict[str, Any]:
    url = f"{base_url()}{path}"
    try:
        resp = requests.post(url, params=params or {}, timeout=_TIMEOUT_SECONDS)
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
        resp = requests.get(url, timeout=10)
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
    return _post(f"/api/sync/{name}")


def sync_due() -> dict[str, Any]:
    return _post("/api/sync_due")


def backfill(name: str, start: str | None, end: str | None) -> dict[str, Any]:
    params = {}
    if start:
        params["from"] = start
    if end:
        params["to"] = end
    return _post(f"/api/backfill/{name}", params=params)


def health() -> dict[str, Any]:
    return _get("/api/health")
