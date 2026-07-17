"""Local daemon auth token: `<root>/state/daemon.token`.

The daemon is the only writer-of-record and its HTTP API has no other
perimeter (it binds to loopback only, but any local process — or a malicious
web page via DNS rebinding — can reach loopback). `ensure_token` generates a
random token on first use and persists it so every subsequent daemon start,
CLI invocation, and MCP call can authenticate as "the local user" without a
login flow.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from personal_db.core.config import Config

TOKEN_FILE_NAME = "daemon.token"
_TOKEN_BYTES = 32


def token_path(cfg: Config) -> Path:
    return cfg.state_dir / TOKEN_FILE_NAME


def read_token(cfg: Config) -> str | None:
    """Return the persisted token, or None if it hasn't been generated yet."""
    path = token_path(cfg)
    if not path.is_file():
        return None
    value = path.read_text().strip()
    return value or None


def ensure_token(cfg: Config) -> str:
    """Return the daemon's auth token, generating and persisting it if absent.

    Written atomically (tmp file + rename) with mode 0600, mirroring the
    pattern in `services/wizard/env_file.py::upsert_env`.
    """
    existing = read_token(cfg)
    if existing:
        return existing

    token = secrets.token_urlsafe(_TOKEN_BYTES)
    path = token_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(token)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)
    return token
