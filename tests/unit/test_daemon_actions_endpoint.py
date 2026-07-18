from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.services.daemon.http import build_app
from tests._daemon_auth import auth_headers


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    cfg = Config(root=tmp_path / "personal_db")
    # Create the dirs the daemon expects (state, trackers, etc.)
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    # Stub installed tracker with an actions.py exposing a known handler.
    tracker_dir = cfg.trackers_dir / "stub"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "manifest.yaml").write_text(
        "name: stub\ndescription: x\npermission_type: none\nschema:\n  tables: {}\n"
    )
    (tracker_dir / "actions.py").write_text(
        "def hello(cfg):\n    return {'ok': True, 'message': 'hi'}\n"
        "def echo(cfg, payload):\n    return {'ok': True, 'payload': payload}\n"
        "def nested_asyncio(cfg):\n"
        "    import asyncio\n"
        "    async def inner():\n"
        "        return {'ok': True, 'message': 'threaded'}\n"
        "    return asyncio.run(inner())\n"
        "def boom(cfg):\n    raise RuntimeError('intentional')\n"
    )

    app = build_app(cfg)
    return TestClient(app, headers=auth_headers(cfg))


def test_calls_tracker_action(client: TestClient) -> None:
    r = client.post("/api/v1/trackers/stub/actions/hello")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["message"] == "hi"


def test_tracker_action_rejects_cross_origin_write(client: TestClient) -> None:
    r = client.post(
        "/api/v1/trackers/stub/actions/hello",
        headers={"origin": "http://attacker.example"},
    )
    assert r.status_code == 403


def test_calls_tracker_action_with_json_payload(client: TestClient) -> None:
    r = client.post("/api/v1/trackers/stub/actions/echo", json={"x": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["payload"] == {"x": 1}


def test_calls_sync_action_in_worker_thread(client: TestClient) -> None:
    r = client.post("/api/v1/trackers/stub/actions/nested_asyncio")
    assert r.status_code == 200
    assert r.json()["message"] == "threaded"


def test_unknown_tracker_404(client: TestClient) -> None:
    r = client.post("/api/v1/trackers/no_such_tracker/actions/hello")
    assert r.status_code == 404


def test_unknown_action_404(client: TestClient) -> None:
    r = client.post("/api/v1/trackers/stub/actions/nope")
    assert r.status_code == 404


def test_handler_exception_500_with_message(client: TestClient) -> None:
    r = client.post("/api/v1/trackers/stub/actions/boom")
    assert r.status_code == 500
    assert "intentional" in r.json()["detail"]


def test_actions_module_import_error_500(tmp_path: Path) -> None:
    """If actions.py raises during import, return 500 with the message."""
    cfg = Config(root=tmp_path / "personal_db")
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    tracker_dir = cfg.trackers_dir / "broken"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "actions.py").write_text("raise ImportError('top-level fail')\n")

    app = build_app(cfg)
    client = TestClient(app, headers=auth_headers(cfg))
    r = client.post("/api/v1/trackers/broken/actions/anything")
    assert r.status_code == 500
    assert "top-level fail" in r.json()["detail"]


def test_path_traversal_rejected(client: TestClient) -> None:
    """Validate that names with .. or other invalid chars are rejected with 400."""
    r = client.post("/api/v1/trackers/..%2Fevil/actions/hello")
    # FastAPI/Starlette URL-decodes `..` and passes it through; _validate_name catches it.
    assert r.status_code in (400, 404)


def test_tracker_action_writes_audit_log_row(tmp_path: Path) -> None:
    cfg = Config(root=tmp_path / "personal_db")
    init_db(cfg.db_path)
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    tracker_dir = cfg.trackers_dir / "stub"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "manifest.yaml").write_text(
        "name: stub\ndescription: x\npermission_type: none\nschema:\n  tables: {}\n"
    )
    (tracker_dir / "actions.py").write_text(
        "def hello(cfg):\n    return {'ok': True, 'message': 'hi'}\n"
        "def boom(cfg):\n    raise RuntimeError('intentional')\n"
    )

    app = build_app(cfg)
    client = TestClient(app, headers=auth_headers(cfg))

    ok = client.post("/api/v1/trackers/stub/actions/hello")
    assert ok.status_code == 200
    failed = client.post("/api/v1/trackers/stub/actions/boom")
    assert failed.status_code == 500

    con = sqlite3.connect(cfg.db_path)
    rows = con.execute(
        "SELECT surface, extension, action, result FROM action_log ORDER BY id"
    ).fetchall()
    con.close()
    assert rows[0] == ("tracker_action", "stub", "hello", "ok")
    assert rows[1][:3] == ("tracker_action", "stub", "boom")
    assert rows[1][3].startswith("error:")


def test_app_action_writes_audit_log_row(tmp_path: Path) -> None:
    cfg = Config(root=tmp_path / "personal_db")
    init_db(cfg.db_path)
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    app_dir = cfg.apps_dir / "stub_app"
    app_dir.mkdir(parents=True)
    (app_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "stub_app",
                "title": "Stub App",
                "pages": [{"slug": "home", "title": "Home", "view": "render_home"}],
                "writes": {"actions": ["ping"]},
            }
        )
    )
    (app_dir / "views.py").write_text(
        "def render_home(ctx):\n    return '<p>stub</p>'\n"
    )
    (app_dir / "actions.py").write_text(
        "def ping(ctx):\n    return {'ok': True}\n"
    )

    app = build_app(cfg)
    client = TestClient(app, headers=auth_headers(cfg))
    r = client.post("/api/v1/apps/stub_app/actions/ping", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    con = sqlite3.connect(cfg.db_path)
    row = con.execute(
        "SELECT surface, extension, action, result FROM action_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert row == ("app_action", "stub_app", "ping", "ok")
