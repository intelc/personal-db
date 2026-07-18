"""Browser session bootstrap: `/auth`, `/auth/session`, `/auth/bootstrap`.

Design (see the Phase 2a plan for the full rationale): the daemon's token
must never appear in a URL, so a bare browser tab pointed at the dashboard
has no way to authenticate itself directly. Two paths in:

1. **Manual**: `GET /auth` renders a small unauthenticated form. The user
   pastes the token from `<root>/state/daemon.token` and `POST /auth/session`
   validates it (constant-time compare) and sets the `pdb_session` cookie.
2. **Launcher-assisted**: something that already holds the token file (the
   CLI `ui` command, the menubar, the setup wizard's finish step) calls
   `POST /api/v1/auth/otc` (itself token-authenticated) to mint a single-use,
   30-second one-time code, then opens the browser at
   `/auth/bootstrap?otc=<code>`. That handler redeems the code and sets the
   same cookie. The OTC's short TTL and immediate invalidation-on-use make a
   leaked URL (shell history, browser history) harmless.

Both routes end by redirecting to `next` (default `/`), never by rendering
the destination page directly, so the browser's address bar and history
never end up with a raw token or OTC lingering past first use.
"""

from __future__ import annotations

import html
import urllib.parse
from typing import Any

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from personal_db.services.daemon.auth import COOKIE_NAME, token_matches
from personal_db.services.daemon.otc import OtcStore

# 400 days: comfortably under browsers' de-facto cookie lifetime caps while
# staying "effectively persistent" for a local trusted device. Revocation
# story is deleting/rotating `state/daemon.token`, which invalidates every
# outstanding cookie immediately since the cookie's value *is* the token.
_COOKIE_MAX_AGE_SECONDS = 400 * 24 * 60 * 60


def _safe_next(next_: str) -> str:
    """Only allow same-app relative redirects — never an absolute/external URL."""
    if next_ and next_.startswith("/") and not next_.startswith("//"):
        return next_
    return "/"


def _auth_page_html(*, next_: str, msg: str = "") -> str:
    safe_next = html.escape(_safe_next(next_), quote=True)
    msg_html = f'<p class="error">{html.escape(msg)}</p>' if msg else ""
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>personal_db — sign in</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 32rem;
          margin: 4rem auto; padding: 0 1rem; color: #222; }}
  code {{ background: #f0f0f0; padding: 0.1rem 0.3rem; border-radius: 3px; }}
  input[type=password] {{ width: 100%; padding: 0.5rem; font-size: 1rem;
                           box-sizing: border-box; margin: 0.5rem 0; }}
  button {{ padding: 0.5rem 1rem; font-size: 1rem; }}
  .error {{ color: #b00020; }}
</style>
</head>
<body>
  <h1>personal_db</h1>
  <p>Run <code>personal-db ui</code> to open the dashboard with one click,
     or paste the token from <code>&lt;root&gt;/state/daemon.token</code> below.</p>
  {msg_html}
  <form method="post" action="/auth/session">
    <input type="hidden" name="next" value="{safe_next}">
    <input type="password" name="token" placeholder="daemon token" autofocus>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
"""


def register_auth_routes(
    app: FastAPI,
    router: APIRouter,
    *,
    token: str,
    otc_store: OtcStore,
    verify_same_origin_write,
) -> None:
    @app.get("/auth", response_class=HTMLResponse)
    async def auth_page(next: str = "/", msg: str = "") -> HTMLResponse:
        return HTMLResponse(_auth_page_html(next_=next, msg=msg))

    @app.post("/auth/session")
    async def auth_session(
        request: Request,
        token_field: str = Form("", alias="token"),
        next: str = Form("/"),
    ):
        verify_same_origin_write(request)
        if not token_matches(token_field, token):
            query = urllib.parse.urlencode({"next": next, "msg": "invalid token"})
            return RedirectResponse(url=f"/auth?{query}", status_code=303)
        response = RedirectResponse(url=_safe_next(next), status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=_COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/auth/bootstrap")
    async def auth_bootstrap(otc: str = "", next: str = "/"):
        if not otc or not otc_store.redeem(otc):
            query = urllib.parse.urlencode(
                {"next": next, "msg": "one-time code expired or already used"}
            )
            return RedirectResponse(url=f"/auth?{query}", status_code=303)
        response = RedirectResponse(url=_safe_next(next), status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=_COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.post("/auth/otc")
    async def api_auth_otc() -> dict[str, Any]:
        code = otc_store.issue()
        return {"otc": code, "expires_in": otc_store.ttl_seconds}
