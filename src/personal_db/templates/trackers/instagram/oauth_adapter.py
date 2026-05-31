"""Instagram-specific OAuth token adapter.

Instagram's OAuth deviates from RFC 6749 in three ways that matter here:

1. Initial code-exchange against api.instagram.com only yields a short-lived
   (~1h) token. To get a long-lived (60d) token, an additional GET to
   graph.instagram.com is required. This adapter does that hop inside
   `exchange_code`, so callers see a single "exchange and you're done" step.

2. Refresh is a GET (not POST) and takes the current long-lived access
   token, not a separate `refresh_token` field. IG's model is "use the
   token to mint a new copy of itself."

3. Refresh responses do not include a `refresh_token`. To play well with
   personal_db's standard `refresh_if_needed` dispatcher, we set
   `refresh_token = access_token` in both flows so the dispatcher always
   has a working credential to pass back into the next refresh.
"""

from __future__ import annotations

from typing import Any

import requests

SHORT_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
LONG_TOKEN_URL = "https://graph.instagram.com/access_token"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"


class InstagramAdapter:
    def exchange_code(
        self,
        *,
        token_url: str,  # unused — IG has two endpoints
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        r = requests.post(
            SHORT_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=15,
        )
        r.raise_for_status()
        short = r.json()
        short_token = short["access_token"]

        r2 = requests.get(
            LONG_TOKEN_URL,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": client_secret,
                "access_token": short_token,
            },
            timeout=15,
        )
        r2.raise_for_status()
        long_ = r2.json()
        access_token = long_["access_token"]
        return {
            "access_token": access_token,
            "refresh_token": access_token,
            "expires_in": int(long_.get("expires_in", 5184000)),
            "token_type": long_.get("token_type", "Bearer"),
            "user_id": short.get("user_id"),
        }

    def refresh_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]:
        r = requests.get(
            REFRESH_URL,
            params={
                "grant_type": "ig_refresh_token",
                "access_token": refresh_token,
            },
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        access_token = body["access_token"]
        return {
            "access_token": access_token,
            "refresh_token": access_token,
            "expires_in": int(body.get("expires_in", 5184000)),
            "token_type": body.get("token_type", "Bearer"),
        }
