import yaml
from fastapi.testclient import TestClient

from personal_db.config import Config
from personal_db.daemon.http import build_app
from personal_db.db import init_db


def _make_runnable(tmp_root, name="runnable"):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump({
            "name": name,
            "description": "runnable",
            "permission_type": "none",
            "setup_steps": [],
            "schedule": {"every": "1h"},
            "time_column": "ts",
            "granularity": "event",
            "schema": {"tables": {name: {"columns": {
                "id": {"type": "TEXT", "semantic": "id"},
                "ts": {"type": "TEXT", "semantic": "ts"},
            }}}},
        })
    )
    (d / "schema.sql").write_text(
        f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, ts TEXT);"
    )
    (d / "ingest.py").write_text(
        "def backfill(t, start, end):\n"
        "    t.upsert(t.name, [{'id': 'b1', 'ts': '2026-04-01'}], key=['id'])\n"
        "def sync(t):\n"
        "    t.upsert(t.name, [{'id': 's1', 'ts': '2026-04-25'}], key=['id'])\n"
    )
    return cfg


def test_health_returns_ok(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_sync_one_route(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.post("/api/sync/runnable")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tracker"] == "runnable"


def test_sync_one_unknown_tracker_404(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.post("/api/sync/nope")
    assert r.status_code == 404


def test_sync_one_invalid_name_400(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.post("/api/sync/..%2Fescape")
    # FastAPI may decode the path; either rejection (400) or 404 is acceptable
    # — what matters is we don't 500 or actually run anything.
    assert r.status_code in (400, 404)


def test_sync_due_route(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.post("/api/sync_due")
    assert r.status_code == 200
    assert r.json()["results"]["runnable"] == "ok"


def test_backfill_route(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.post("/api/backfill/runnable", params={"from": "2026-04-01", "to": "2026-04-02"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_sync_one_plaintext_invalid_name_400(tmp_root):
    """Direct exercise of _validate_name's 400 path with a name that won't be intercepted by URL routing."""
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg))
    r = client.post("/api/sync/has-dash")
    assert r.status_code == 400
    assert "invalid tracker name" in r.json()["detail"].lower()
