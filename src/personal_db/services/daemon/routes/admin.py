"""Admin-only daemon control: currently just a graceful self-shutdown.

Exists to fix the "zombie daemon after self-update" bug: the Tauri shell
spawns the daemon as a sidecar (see `shell/src-tauri/src/daemon.rs`), but a
self-update restarts the *shell* process, not the daemon -- nothing kills the
old sidecar. Since Jinja templates are read from disk per request, the old
process goes on serving newly-updated templates against its stale Python
routes (new endpoints 404, deferred viz fetches fail). `POST
/api/v1/admin/shutdown` lets the shell (or anything else holding a valid
daemon token -- this route is token-authed like every other non-exempt route,
see `services/daemon/auth.py::EXEMPT_ROUTES`) ask a stale daemon to exit
itself cleanly so a fresh sidecar can take its place.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from fastapi import APIRouter

# Gives uvicorn a moment to finish flushing the {"ok": true, ...} response
# body to the caller before the process starts tearing down -- exiting
# inline (before returning) would race the response against the socket
# close.
_SHUTDOWN_DELAY_SECONDS = 0.2


def _schedule_exit() -> None:
    """Ask this process to exit shortly after the current response flushes.

    SIGTERM (not `os._exit`) gives uvicorn's own signal handler a chance to
    close the listening socket and any in-flight connections cleanly rather
    than yanking the process out from under them; `call_later` on the
    running event loop (rather than an inline `os.kill`) is what creates the
    "after this response flushes" ordering -- the callback runs on a later
    iteration of the same loop, once the response has already been written.

    Split out from the route body so tests can monkeypatch it directly and
    assert it was *scheduled*, without waiting out the delay or risking
    actually killing the test process.
    """
    loop = asyncio.get_event_loop()
    loop.call_later(_SHUTDOWN_DELAY_SECONDS, os.kill, os.getpid(), signal.SIGTERM)


def register_admin_routes(router: APIRouter) -> None:
    @router.post("/admin/shutdown")
    async def api_admin_shutdown() -> dict[str, Any]:
        _schedule_exit()
        return {"ok": True, "shutting_down": True}
