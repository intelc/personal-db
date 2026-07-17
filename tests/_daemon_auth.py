"""Shared test helper for building an authenticated daemon TestClient.

Phase 2a requires a valid token (Bearer/X-PDB-Token/session cookie) on every
daemon route except GET /api/health. Tests that build a `TestClient` directly
against `build_app` need to carry that token so they keep exercising route
behavior rather than tripping the 401 gate incidentally. Dedicated auth tests
(tests/unit/test_daemon_auth.py) cover the unauthenticated-401 behavior itself.
"""

from __future__ import annotations

from personal_db.core.config import Config
from personal_db.core.daemon_token import ensure_token


def auth_headers(cfg: Config) -> dict[str, str]:
    """Bearer header carrying `cfg`'s daemon token, generating it if needed."""
    return {"Authorization": f"Bearer {ensure_token(cfg)}"}
