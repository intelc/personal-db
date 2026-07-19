"""POST /api/v1/admin/shutdown: token-authed, graceful daemon self-exit.

Fixes the "zombie daemon after self-update" bug -- see
`services/daemon/routes/admin.py`'s module docstring for the full story.
These tests cover the route's HTTP contract (401 unauthenticated, 200 +
scheduled exit with a valid token) without ever letting the exit mechanism
actually fire in the test process; `_schedule_exit` is monkeypatched so we
can assert it was *called* (i.e. the exit was scheduled) rather than racing
the real 0.2s `call_later` delay against the TestClient's short-lived event
loop.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.services.daemon.http import build_app
from personal_db.services.daemon.routes import admin as admin_routes
from tests._daemon_auth import auth_headers


def _base_cfg(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def test_shutdown_requires_auth(tmp_root):
    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.post("/api/v1/admin/shutdown")
    assert r.status_code == 401


def test_shutdown_with_valid_token_returns_ok_and_schedules_exit(tmp_root, monkeypatch):
    cfg = _base_cfg(tmp_root)
    scheduled: list[bool] = []
    monkeypatch.setattr(admin_routes, "_schedule_exit", lambda: scheduled.append(True))

    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/v1/admin/shutdown")

    assert r.status_code == 200
    assert r.json() == {"ok": True, "shutting_down": True}
    assert scheduled == [True]


def test_shutdown_wrong_token_is_401(tmp_root):
    cfg = _base_cfg(tmp_root)
    from personal_db.core.daemon_token import ensure_token

    ensure_token(cfg)
    client = TestClient(build_app(cfg), headers={"Authorization": "Bearer nope"})
    r = client.post("/api/v1/admin/shutdown")
    assert r.status_code == 401


def test_shutdown_rejects_cross_origin_write(tmp_root):
    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post(
        "/api/v1/admin/shutdown",
        headers={"origin": "http://attacker.example"},
    )
    assert r.status_code == 403
