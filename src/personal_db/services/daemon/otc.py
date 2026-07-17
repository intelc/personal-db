"""One-time codes for the browser session bootstrap (see routes/auth.py).

Launchers that hold the token file (CLI `ui`, menubar, wizard finish, setup's
browser-mode server) can authenticate over HTTP directly, but a freshly
opened browser tab has no way to attach an `Authorization` header. Rather
than putting the long-lived token in a URL (logged in shell history, browser
history, proxy logs...), a launcher exchanges the token for a single-use,
30-second-lived OTC via `POST /api/auth/otc`, then opens the browser at
`/auth/bootstrap?otc=<code>`. Immediate invalidation on redemption (success
or failure) plus the short TTL means a leaked URL is worthless almost
immediately.
"""

from __future__ import annotations

import secrets
import threading
import time

DEFAULT_TTL_SECONDS = 30.0


class OtcStore:
    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._codes: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def issue(self) -> str:
        code = secrets.token_urlsafe(24)
        with self._lock:
            self._prune_locked()
            self._codes[code] = time.time() + self._ttl
        return code

    def redeem(self, code: str) -> bool:
        """Consume `code` if valid. Always single-use: removed whether or not
        it was still live, so a retried/leaked code never validates twice."""
        with self._lock:
            expires_at = self._codes.pop(code, None)
        if expires_at is None:
            return False
        return time.time() < expires_at

    def _prune_locked(self) -> None:
        now = time.time()
        expired = [code for code, expires_at in self._codes.items() if expires_at <= now]
        for code in expired:
            del self._codes[code]
