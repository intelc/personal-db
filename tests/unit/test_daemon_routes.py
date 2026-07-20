import pytest
import yaml
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, init_db
from personal_db.core.installer import install_template
from personal_db.services.daemon.agent_terminal import build_cli_command
from personal_db.services.daemon.http import build_app
from tests._agent_terminal_helpers import enable_agent_terminal
from tests._daemon_auth import auth_headers
from tests._validation_helpers import mark_valid


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
    mark_valid(cfg, name)
    return cfg


def test_health_returns_ok(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_rejects_untrusted_host_header(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/health", headers={"host": "attacker.example:8765"})
    assert r.status_code == 400
    assert "host" in r.json()["detail"].lower()


def test_sync_one_route(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/v1/sync/runnable")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tracker"] == "runnable"


def test_sync_one_rejects_cross_origin_write(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/v1/sync/runnable", headers={"origin": "http://attacker.example"})
    assert r.status_code == 403


def test_sync_one_unknown_tracker_404(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/v1/sync/nope")
    assert r.status_code == 404


def test_sync_one_invalid_name_400(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/v1/sync/..%2Fescape")
    # FastAPI may decode the path; either rejection (400) or 404 is acceptable
    # — what matters is we don't 500 or actually run anything.
    assert r.status_code in (400, 404)


def test_sync_due_route(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/v1/sync_due")
    assert r.status_code == 200
    assert r.json()["results"]["runnable"] == "ok"


def test_backfill_route(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/v1/backfill/runnable", params={"from": "2026-04-01", "to": "2026-04-02"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_agent_context_dashboard_route(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/agent/context", params={"path": "/"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "dashboard"
    assert body["path"] == "/"
    assert "runnable" in body["trackers"]
    assert body["dashboard_api"]["app_query_pattern"] == "/api/v1/apps/{app}/queries/{query}"


def test_agent_context_tracker_route(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/agent/context", params={"path": "/t/runnable"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "tracker"
    assert body["tracker"]["name"] == "runnable"


def test_agent_session_lifecycle_uses_configured_cli(tmp_root, monkeypatch):
    monkeypatch.setenv("PERSONAL_DB_CLAUDE_COMMAND", "true")
    cfg = _make_runnable(tmp_root)
    enable_agent_terminal(cfg)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    created = client.post(
        "/api/v1/agent/sessions",
        json={"cli_type": "claude", "context": {"page": {"path": "/"}}, "cols": 80, "rows": 24},
    )
    assert created.status_code == 200
    session = created.json()["session"]
    assert session["cli_type"] == "claude"

    listed = client.get("/api/v1/agent/sessions")
    assert listed.status_code == 200
    assert any(item["id"] == session["id"] for item in listed.json()["sessions"])

    deleted = client.delete(f"/api/v1/agent/sessions/{session['id']}")
    assert deleted.status_code == 200


def test_agent_cli_commands_default_to_no_bypass_flags(monkeypatch):
    """auto_approve defaults to False (config.yaml: agent_terminal.auto_approve) --
    without it, the CLI spawns with its normal interactive approval prompts."""
    monkeypatch.delenv("PERSONAL_DB_CLAUDE_COMMAND", raising=False)
    monkeypatch.delenv("PERSONAL_DB_CODEX_COMMAND", raising=False)

    claude = build_cli_command("claude", "hello")
    codex = build_cli_command("codex", "hello")

    assert claude == "claude hello"
    assert codex == "codex --no-alt-screen hello"


def test_agent_cli_commands_use_bypass_flags_when_auto_approve(monkeypatch):
    monkeypatch.delenv("PERSONAL_DB_CLAUDE_COMMAND", raising=False)
    monkeypatch.delenv("PERSONAL_DB_CODEX_COMMAND", raising=False)

    claude = build_cli_command("claude", "hello", auto_approve=True)
    codex = build_cli_command("codex", "hello", auto_approve=True)

    assert claude == "claude --permission-mode auto hello"
    assert codex == "codex --no-alt-screen --dangerously-bypass-approvals-and-sandbox hello"


def test_sync_one_plaintext_invalid_name_400(tmp_root):
    """Direct exercise of _validate_name's 400 path with a name that won't be intercepted by URL routing."""
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.post("/api/v1/sync/has-dash")
    assert r.status_code == 400
    assert "invalid tracker name" in r.json()["detail"].lower()


@pytest.mark.darwin_only  # installs the darwin-gated life_context tracker
def test_log_life_context_route_accepts_past_date(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    dest = install_template(cfg, "life_context")
    apply_tracker_schema(cfg.db_path, (dest / "schema.sql").read_text())
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))

    r = client.post(
        "/log_life_context",
        data={
            "start_date": "2026-06-04",
            "state": "traveling",
            "note": "flew to Japan",
        },
        headers={"referer": "/t/life_context"},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert r.headers["location"] == "/t/life_context"

    import sqlite3

    con = sqlite3.connect(cfg.db_path)
    row = con.execute(
        "SELECT date, state, note FROM life_context"
    ).fetchone()
    con.close()
    assert row == ("2026-06-04", "traveling", "flew to Japan")


def test_connector_prompt_route_happy_path(tmp_root):
    """Not gated on agent_terminal.enabled -- reachable even with the
    terminal off, since it's just text a UI/external agent can consume."""
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/agent/connector-prompt", params={"slug": "runnable"})
    assert r.status_code == 200
    body = r.json()
    assert "runnable" in body["prompt"]
    assert "{{slug}}" not in body["prompt"]


def test_connector_prompt_route_rejects_bad_slug(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/agent/connector-prompt", params={"slug": "Has-Dash"})
    assert r.status_code == 400


def test_connector_prompt_route_404s_for_missing_tracker(tmp_root):
    cfg = _make_runnable(tmp_root)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/agent/connector-prompt", params={"slug": "nope_not_installed"})
    assert r.status_code == 404
