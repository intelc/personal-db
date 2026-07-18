"""Phase 3: /api/v1 versioning — health fields, legacy 308 redirects
(method-preserving), and auth enforcement on the new prefix."""

from __future__ import annotations

from personal_db.core.config import Config
from personal_db.core.daemon_token import ensure_token
from personal_db.services.daemon import auth as auth_mod
from personal_db.services.daemon.http import build_app
from tests._daemon_auth import auth_headers


def _base_cfg(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def test_health_reports_app_api_and_db_versions(tmp_root):
    cfg = _base_cfg(tmp_root)
    from fastapi.testclient import TestClient

    client = TestClient(build_app(cfg))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["api_version"] == 1
    assert isinstance(body["app_version"], str) and body["app_version"]
    assert body["db_user_version"] == 0  # no db.sqlite created yet in this root
    assert body["status"] == "ok"


def test_health_reports_db_user_version_once_db_exists(tmp_root):
    from personal_db.core.db import CORE_SCHEMA_VERSION, init_db
    from fastapi.testclient import TestClient

    cfg = _base_cfg(tmp_root)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg))
    body = client.get("/api/v1/health").json()
    assert body["db_user_version"] == CORE_SCHEMA_VERSION


def test_legacy_health_redirects_308_to_v1(tmp_root):
    from fastapi.testclient import TestClient

    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.get("/api/health", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/api/v1/health"

    followed = client.get("/api/health", follow_redirects=True)
    assert followed.status_code == 200
    assert followed.json()["api_version"] == 1


def test_legacy_redirect_preserves_post_method_and_body_effect(tmp_root):
    from fastapi.testclient import TestClient

    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/sync_due", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/api/v1/sync_due"

    followed = client.post("/api/sync_due", follow_redirects=True)
    assert followed.status_code == 200
    assert "results" in followed.json()


def test_legacy_redirect_preserves_query_string(tmp_root):
    from fastapi.testclient import TestClient

    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/backfill/nope?from=2026-01-01&to=2026-01-02", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/api/v1/backfill/nope?from=2026-01-01&to=2026-01-02"


def test_unknown_legacy_api_path_404s_without_looping(tmp_root):
    from fastapi.testclient import TestClient

    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/nonexistent-route", follow_redirects=True)
    assert r.status_code == 404


def test_unknown_v1_path_404s_directly_not_redirected(tmp_root):
    from fastapi.testclient import TestClient

    cfg = _base_cfg(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/nonexistent-route", follow_redirects=False)
    assert r.status_code == 404


def test_legacy_sync_due_401_without_token(tmp_root):
    from fastapi.testclient import TestClient

    cfg = _base_cfg(tmp_root)
    ensure_token(cfg)
    client = TestClient(build_app(cfg))
    assert client.post("/api/sync_due").status_code == 401


def test_v1_sync_due_401_without_token_200_with(tmp_root):
    from fastapi.testclient import TestClient

    cfg = _base_cfg(tmp_root)
    ensure_token(cfg)
    anon = TestClient(build_app(cfg))
    assert anon.post("/api/v1/sync_due").status_code == 401

    authed = TestClient(build_app(cfg), headers=auth_headers(cfg))
    assert authed.post("/api/v1/sync_due").status_code == 200


def test_exempt_table_covers_both_health_paths():
    assert auth_mod.is_exempt("GET", "/api/v1/health")
    assert auth_mod.is_exempt("GET", "/api/health")
    assert not auth_mod.is_exempt("GET", "/api/v1/sync_due")
    assert not auth_mod.is_exempt("POST", "/api/v1/health")


def test_app_action_urls_are_versioned(tmp_root):
    from personal_db.core.apps import AppContext, AppManifest, AppPage

    cfg = _base_cfg(tmp_root)
    manifest = AppManifest(
        name="sample",
        title="Sample",
        description="",
        pages=(AppPage(slug="home", title="Home", view="render_home"),),
    )
    ctx = AppContext(cfg=cfg, app_dir=cfg.apps_dir / "sample", manifest=manifest)
    assert ctx.query_url("rows") == "/api/v1/apps/sample/queries/rows"
    assert ctx.model_url("summary") == "/api/v1/apps/sample/models/summary"
    assert ctx.action_url("do_thing") == "/api/v1/apps/sample/actions/do_thing"
