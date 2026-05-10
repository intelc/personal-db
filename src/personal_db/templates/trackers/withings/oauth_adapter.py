"""Withings-specific OAuth token adapter.

Withings deviates from RFC 6749 in two ways: (1) every token request needs
an extra `action=requesttoken` form param; (2) responses are wrapped in
`{"status": 0, "body": {...}}` and a non-zero status means error.

This adapter handles both, returning a flat token dict in the shape that
personal_db.oauth expects (access_token / refresh_token / expires_in).
"""

from __future__ import annotations

from typing import Any

import requests


class WithingsAdapter:
    TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"

    def exchange_code(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        return self._post(
            {
                "action": "requesttoken",
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            }
        )

    def refresh_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]:
        return self._post(
            {
                "action": "requesttoken",
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }
        )

    def _post(self, data: dict) -> dict[str, Any]:
        r = requests.post(self.TOKEN_URL, data=data, timeout=10)
        r.raise_for_status()
        envelope = r.json()
        if envelope.get("status") != 0:
            raise RuntimeError(f"Withings token error: {envelope}")
        body = envelope.get("body") or {}
        return {
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "expires_in": int(body.get("expires_in", 10800)),
            "userid": body.get("userid"),
            "scope": body.get("scope"),
            "token_type": body.get("token_type", "Bearer"),
        }
