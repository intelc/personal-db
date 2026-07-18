"""Token-auth primitives shared by the HTTP middleware and the agent websocket.

See routes/auth.py for the browser bootstrap flow (the `/auth`, `/auth/session`,
`/auth/bootstrap` routes) and core/daemon_token.py for how the token itself is
generated/persisted.
"""

from __future__ import annotations

import secrets
from typing import Any

COOKIE_NAME = "pdb_session"
TOKEN_HEADER = "X-PDB-Token"

# (method, path) pairs reachable with no token at all. Kept to the bare
# minimum needed to bootstrap a session plus the liveness probe — everything
# else (including the dashboard HTML, every /api/* route, and the agent
# websocket) requires a valid token.
EXEMPT_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/health"),
        # Legacy path — remove after next release (see http.py's `/api/{rest}`
        # 308 redirect). Exempting it too keeps the redirect itself reachable
        # without a token, matching the exemption on its target.
        ("GET", "/api/health"),
        ("GET", "/auth"),
        ("POST", "/auth/session"),
        ("GET", "/auth/bootstrap"),
    }
)


def is_exempt(method: str, path: str) -> bool:
    return (method.upper(), path) in EXEMPT_ROUTES


def token_matches(candidate: str | None, expected: str) -> bool:
    if not candidate:
        return False
    return secrets.compare_digest(candidate, expected)


def _bearer_token(headers: Any) -> str | None:
    value = headers.get("authorization", "")
    if value.lower().startswith("bearer "):
        return value[len("bearer ") :].strip()
    return None


def extract_presented_token(request: Any) -> str | None:
    """Pull a candidate token from Authorization, X-PDB-Token, or the session cookie.

    Works for both `fastapi.Request` and `fastapi.WebSocket` — both expose
    `.headers` (case-insensitive mapping) and `.cookies`.
    """
    token = _bearer_token(request.headers)
    if token:
        return token
    token = request.headers.get(TOKEN_HEADER)
    if token:
        return token
    return request.cookies.get(COOKIE_NAME)


def is_authenticated(request: Any, expected: str) -> bool:
    return token_matches(extract_presented_token(request), expected)


def wants_html(request: Any) -> bool:
    """True if the client's Accept header prefers HTML over anything else.

    Used to decide whether an unauthenticated hit gets a friendly redirect to
    `/auth` (real browser navigation) or a bare 401 (curl/scripts/API
    clients) — see build_app's request guard in http.py.
    """
    accept = request.headers.get("accept", "")
    return "text/html" in accept.lower()
