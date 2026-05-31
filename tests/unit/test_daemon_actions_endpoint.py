from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_db.config import Config
from personal_db.daemon.http import build_app


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
    return TestClient(app)


def test_calls_tracker_action(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/hello")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["message"] == "hi"


def test_calls_tracker_action_with_json_payload(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/echo", json={"x": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["payload"] == {"x": 1}


def test_calls_sync_action_in_worker_thread(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/nested_asyncio")
    assert r.status_code == 200
    assert r.json()["message"] == "threaded"


def test_unknown_tracker_404(client: TestClient) -> None:
    r = client.post("/api/trackers/no_such_tracker/actions/hello")
    assert r.status_code == 404


def test_unknown_action_404(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/nope")
    assert r.status_code == 404


def test_handler_exception_500_with_message(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/boom")
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
    client = TestClient(app)
    r = client.post("/api/trackers/broken/actions/anything")
    assert r.status_code == 500
    assert "top-level fail" in r.json()["detail"]


def test_path_traversal_rejected(client: TestClient) -> None:
    """Validate that names with .. or other invalid chars are rejected with 400."""
    r = client.post("/api/trackers/..%2Fevil/actions/hello")
    # FastAPI/Starlette URL-decodes `..` and passes it through; _validate_name catches it.
    assert r.status_code in (400, 404)
