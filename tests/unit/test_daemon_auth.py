"""Phase 2a: token auth on every daemon route (except GET /api/health) plus
the browser session bootstrap (/auth, /auth/session, /auth/bootstrap,
/api/auth/otc) and the agent-terminal websocket.
"""

from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from personal_db.core.config import Config
from personal_db.core.daemon_token import ensure_token, read_token
from personal_db.services.daemon import auth as auth_mod
from personal_db.services.daemon.http import build_app
from personal_db.services.daemon.otc import OtcStore
from tests._agent_terminal_helpers import enable_agent_terminal
from tests._daemon_auth import auth_headers

_PARAM_RE = re.compile(r"\{[^{}]+\}")


def _concrete_path(path: str) -> str:
    return _PARAM_RE.sub("x", path)


def _route_table(app) -> list[tuple[str, str]]:
    """(method, path) for every plain HTTP route. Skips Mount (/static) and
    WebSocketRoute (no `.methods`) — the websocket is exercised separately."""
    entries: list[tuple[str, str]] = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            entries.append((method, route.path))
    return entries


def _base_cfg(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def test_every_route_requires_auth_except_exempt(tmp_root):
    cfg = _base_cfg(tmp_root)
    app = build_app(cfg)
    anon = TestClient(app)  # no Authorization/cookie

    checked = 0
    for method, path in _route_table(app):
        concrete = _concrete_path(path)
        if auth_mod.is_exempt(method, path) or auth_mod.is_exempt(method, concrete):
            continue
        resp = anon.request(method, concrete)
        assert resp.status_code == 401, f"{method} {concrete} -> {resp.status_code}, expected 401"
        checked += 1
    assert checked > 10, "sanity check: route table introspection found suspiciously few routes"


def test_health_is_exempt(tmp_root):
    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.get("/api/health")
    assert r.status_code == 200


def test_valid_bearer_token_authenticates(tmp_root):
    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/sync_due").status_code == 200


def test_valid_x_pdb_token_header_authenticates(tmp_root):
    cfg = _base_cfg(tmp_root)
    token = ensure_token(cfg)
    client = TestClient(build_app(cfg), headers={"X-PDB-Token": token})
    assert client.post("/api/sync_due").status_code == 200


def test_wrong_bearer_token_is_401(tmp_root):
    cfg = _base_cfg(tmp_root)
    ensure_token(cfg)
    client = TestClient(build_app(cfg), headers={"Authorization": "Bearer not-the-token"})
    assert client.get("/").status_code == 401


def test_unauthenticated_browser_request_redirects_to_auth_page(tmp_root):
    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/auth")


def test_auth_session_valid_token_sets_cookie_and_grants_access(tmp_root):
    cfg = _base_cfg(tmp_root)
    token = ensure_token(cfg)
    client = TestClient(build_app(cfg))
    r = client.post(
        "/auth/session",
        data={"token": token, "next": "/api/health"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/api/health"
    assert auth_mod.COOKIE_NAME in client.cookies

    # The cookie alone (no bearer header) now authenticates every route.
    r2 = client.post("/api/sync_due")
    assert r2.status_code == 200


def test_auth_session_invalid_token_redirects_back_to_auth(tmp_root):
    cfg = _base_cfg(tmp_root)
    ensure_token(cfg)
    client = TestClient(build_app(cfg))
    r = client.post(
        "/auth/session", data={"token": "wrong", "next": "/"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/auth")
    assert auth_mod.COOKIE_NAME not in client.cookies


def test_otc_mint_and_bootstrap_grants_cookie(tmp_root):
    cfg = _base_cfg(tmp_root)
    app = build_app(cfg)
    owner = TestClient(app, headers=auth_headers(cfg))

    minted = owner.post("/api/auth/otc")
    assert minted.status_code == 200
    otc = minted.json()["otc"]
    assert minted.json()["expires_in"] > 0

    anon = TestClient(app)
    r = anon.get(f"/auth/bootstrap?otc={otc}&next=/api/health", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/api/health"
    assert auth_mod.COOKIE_NAME in anon.cookies


def test_otc_is_single_use(tmp_root):
    cfg = _base_cfg(tmp_root)
    app = build_app(cfg)
    owner = TestClient(app, headers=auth_headers(cfg))
    otc = owner.post("/api/auth/otc").json()["otc"]

    first = TestClient(app)
    r1 = first.get(f"/auth/bootstrap?otc={otc}", follow_redirects=False)
    assert r1.status_code == 303
    assert r1.headers["location"] == "/"

    second = TestClient(app)
    r2 = second.get(f"/auth/bootstrap?otc={otc}", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"].startswith("/auth")


def test_otc_store_expires_after_ttl():
    store = OtcStore(ttl_seconds=0.01)
    code = store.issue()
    time.sleep(0.03)
    assert store.redeem(code) is False


def test_otc_store_single_use_directly():
    store = OtcStore(ttl_seconds=30)
    code = store.issue()
    assert store.redeem(code) is True
    assert store.redeem(code) is False


def test_agent_ws_rejects_without_auth(tmp_root, monkeypatch):
    monkeypatch.setenv("PERSONAL_DB_CLAUDE_COMMAND", "true")
    cfg = _base_cfg(tmp_root)
    enable_agent_terminal(cfg)
    app = build_app(cfg)
    owner = TestClient(app, headers=auth_headers(cfg))
    created = owner.post(
        "/api/agent/sessions", json={"cli_type": "claude", "context": {}}
    )
    session_id = created.json()["session"]["id"]

    anon = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect(f"/api/agent/sessions/{session_id}/terminal"):
            pass


def test_agent_ws_accepts_with_bearer_header(tmp_root, monkeypatch):
    monkeypatch.setenv("PERSONAL_DB_CLAUDE_COMMAND", "true")
    cfg = _base_cfg(tmp_root)
    enable_agent_terminal(cfg)
    app = build_app(cfg)
    owner = TestClient(app, headers=auth_headers(cfg))
    created = owner.post(
        "/api/agent/sessions", json={"cli_type": "claude", "context": {}}
    )
    session_id = created.json()["session"]["id"]

    with owner.websocket_connect(f"/api/agent/sessions/{session_id}/terminal"):
        pass


def test_agent_ws_accepts_with_session_cookie(tmp_root, monkeypatch):
    monkeypatch.setenv("PERSONAL_DB_CLAUDE_COMMAND", "true")
    cfg = _base_cfg(tmp_root)
    enable_agent_terminal(cfg)
    token = ensure_token(cfg)
    app = build_app(cfg)

    cookie_client = TestClient(app)
    cookie_client.post("/auth/session", data={"token": token, "next": "/"})

    created = cookie_client.post(
        "/api/agent/sessions", json={"cli_type": "claude", "context": {}}
    )
    session_id = created.json()["session"]["id"]

    with cookie_client.websocket_connect(f"/api/agent/sessions/{session_id}/terminal"):
        pass


def test_ensure_token_persists_and_is_reused(tmp_root):
    cfg = _base_cfg(tmp_root)
    first = ensure_token(cfg)
    second = ensure_token(cfg)
    assert first == second
    assert read_token(cfg) == first
    path = cfg.state_dir / "daemon.token"
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600
