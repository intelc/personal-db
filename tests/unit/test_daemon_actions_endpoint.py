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


def test_unknown_tracker_404(client: TestClient) -> None:
    r = client.post("/api/trackers/no-such-tracker/actions/hello")
    assert r.status_code == 404


def test_unknown_action_404(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/nope")
    assert r.status_code == 404


def test_handler_exception_500_with_message(client: TestClient) -> None:
    r = client.post("/api/trackers/stub/actions/boom")
    assert r.status_code == 500
    assert "intentional" in r.json()["detail"]
