# Sync Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace today's periodic-fork launchd scheduler with a single always-on `personal-db daemon` that hosts the FastAPI dashboard AND serves sync requests over HTTP, so CLI / MCP `sync` calls work from any process regardless of macOS Full Disk Access attribution.

**Architecture:** One launchd-managed long-running process (`KeepAlive=true`) on `127.0.0.1:8765` exposes `/api/sync/*` routes plus the existing dashboard. CLI and MCP `sync` callers become thin HTTP clients (using `requests`); on connection refused they hard-fail with `personal-db daemon not running. Run \`personal-db daemon install\``. The dashboard moves out of `personal-db ui`, which becomes a menubar-only shell that talks to the daemon over HTTP.

**Tech Stack:** Python 3.11, FastAPI + uvicorn (existing), typer (existing CLI), `requests` (already a dep — used both for the daemon client and wrapped in `asyncio.to_thread` at the MCP boundary), launchd plist (macOS).

**Spec:** `docs/superpowers/specs/2026-05-04-sync-daemon-design.md`

---

## File map

### New
- `src/personal_db/daemon/__init__.py` — empty package marker.
- `src/personal_db/daemon/server.py` — orchestrator: `run(cfg, port)` + `start_periodic_sync(cfg, interval) -> Thread`.
- `src/personal_db/daemon/http.py` — relocated from `ui/server.py`; `build_app(cfg)` now also mounts `/api/sync/*`, `/api/sync_due`, `/api/backfill/*`, `/api/health`.
- `src/personal_db/daemon/client.py` — `DaemonUnreachable` exception; `sync_one(name)`, `sync_due()`, `backfill(name, start, end)`, `health()` functions.
- `src/personal_db/daemon/install.py` — `LABEL`, `OLD_LABEL`, `plist_path()`, `build_plist()`, `install(root)`, `uninstall()`, `status()`. Auto-migrates `com.personal_db.scheduler.plist` → `com.personal_db.daemon.plist`.
- `src/personal_db/cli/daemon_cmd.py` — typer subcommands: `install`, `uninstall`, `status`, `restart`, `run`.

### Modified
- `src/personal_db/cli/sync_cmd.py` — `sync` and `backfill` POST to daemon via `daemon.client`; `DaemonUnreachable` → exit 2.
- `src/personal_db/mcp_server/tools.py` — `sync_tool`, `sync_due_tool`, `backfill_tool` delegate to `daemon.client`. (MCP server.py wraps these in `asyncio.to_thread` so the event loop isn't blocked.)
- `src/personal_db/mcp_server/server.py` — sync/sync_due/backfill tool dispatches wrapped in `await asyncio.to_thread(...)`.
- `src/personal_db/ui/menubar.py` — drop `_start_server` and the dashboard thread; "Force sync" button → `daemon.client.sync_due()`.
- `src/personal_db/cli/ui_cmd.py` — drop `--no-menubar`; runs only the rumps menubar.
- `src/personal_db/cli/main.py` — register `daemon` typer group; remove `scheduler` registration.

### Removed
- `src/personal_db/scheduler.py`
- `src/personal_db/cli/scheduler_cmd.py`
- `src/personal_db/ui/server.py` (relocated to `daemon/http.py`)
- `tests/unit/test_scheduler.py` (replaced by `test_daemon_install.py`)

---

## Task 1: Relocate dashboard server (pure refactor)

**Goal:** Move `src/personal_db/ui/server.py` to `src/personal_db/daemon/http.py` with no behavior change. All existing tests must still pass.

**Files:**
- Create: `src/personal_db/daemon/__init__.py`
- Create: `src/personal_db/daemon/http.py` (content of old `ui/server.py`)
- Delete: `src/personal_db/ui/server.py`
- Modify: `src/personal_db/ui/menubar.py:27` — update import.

- [ ] **Step 1: Create empty package marker.**

Create `src/personal_db/daemon/__init__.py` with empty content.

- [ ] **Step 2: Move ui/server.py → daemon/http.py.**

```bash
git mv src/personal_db/ui/server.py src/personal_db/daemon/http.py
```

No content changes inside the file — it still imports from `personal_db.scheduler` (not yet removed) and exports `build_app(cfg)` unchanged.

- [ ] **Step 3: Update the menubar import.**

In `src/personal_db/ui/menubar.py`, line 27, change:
```python
from personal_db.ui.server import build_app
```
to:
```python
from personal_db.daemon.http import build_app
```

- [ ] **Step 4: Run the existing UI test suite to verify nothing broke.**

Run: `.venv/bin/python -m pytest tests/unit/test_ui_setup.py tests/unit/test_ui_viz.py -q`
Expected: all pass.

- [ ] **Step 5: Run the full test suite.**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (note: `tests/unit/test_scheduler.py` still passes because `scheduler.py` is untouched).

- [ ] **Step 6: Commit.**

```bash
git add src/personal_db/daemon/__init__.py src/personal_db/daemon/http.py src/personal_db/ui/menubar.py
git rm src/personal_db/ui/server.py 2>/dev/null || true  # already gone via git mv
git commit -m "refactor(daemon): relocate dashboard server to daemon package"
```

---

## Task 2: Daemon plist generation + auto-migration

**Goal:** New `daemon/install.py` that generates the long-running plist and auto-migrates the old scheduler plist when present.

**Files:**
- Create: `src/personal_db/daemon/install.py`
- Create: `tests/unit/test_daemon_install.py`

- [ ] **Step 1: Write the failing test.**

Create `tests/unit/test_daemon_install.py`:

```python
from pathlib import Path

import pytest

from personal_db.daemon import install as di


def test_build_plist_contains_label_keepalive_and_args(tmp_path):
    body = di.build_plist(
        pdb_path="/usr/local/bin/personal-db",
        root=tmp_path / "personal_db",
        log_path=tmp_path / "personal_db" / "state" / "daemon.log",
    )
    assert f"<string>{di.LABEL}</string>" in body
    assert "<key>KeepAlive</key><true/>" in body
    assert "<key>RunAtLoad</key><true/>" in body
    assert "<string>/usr/local/bin/personal-db</string>" in body
    assert "<string>daemon</string>" in body
    assert "<string>run</string>" in body
    assert str(tmp_path / "personal_db" / "state" / "daemon.log") in body
    # Should NOT include StartInterval — daemon is long-running, not periodic.
    assert "StartInterval" not in body


def test_install_migrates_old_scheduler_plist(tmp_path, monkeypatch):
    """When the old com.personal_db.scheduler.plist exists, install() should
    unload+delete it before writing the new daemon plist."""
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    old_plist = fake_la / "com.personal_db.scheduler.plist"
    old_plist.write_text("<plist/>")  # contents irrelevant, presence is what matters
    new_plist = fake_la / "com.personal_db.daemon.plist"

    monkeypatch.setattr(di, "_LAUNCHAGENTS_DIR", fake_la)

    calls: list[list[str]] = []
    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
    monkeypatch.setattr(di.subprocess, "run", fake_run)

    di.install(root=tmp_path / "personal_db")

    assert not old_plist.exists(), "old scheduler plist should be deleted"
    assert new_plist.exists(), "new daemon plist should be written"
    # Verify launchctl was asked to unload the old, then load the new.
    cmds = [" ".join(c) for c in calls]
    assert any("unload" in c and "com.personal_db.scheduler" in c for c in cmds)
    assert any("load" in c and "com.personal_db.daemon" in c for c in cmds)


def test_install_when_no_old_plist(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    monkeypatch.setattr(di, "_LAUNCHAGENTS_DIR", fake_la)
    monkeypatch.setattr(di.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    p = di.install(root=tmp_path / "personal_db")
    assert p == fake_la / "com.personal_db.daemon.plist"
    assert p.exists()


def test_uninstall_removes_plist(tmp_path, monkeypatch):
    fake_la = tmp_path / "LaunchAgents"
    fake_la.mkdir()
    new_plist = fake_la / "com.personal_db.daemon.plist"
    new_plist.write_text("<plist/>")
    monkeypatch.setattr(di, "_LAUNCHAGENTS_DIR", fake_la)
    monkeypatch.setattr(di.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    di.uninstall()
    assert not new_plist.exists()
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_install.py -q`
Expected: ImportError / module not found.

- [ ] **Step 3: Implement `daemon/install.py`.**

Create `src/personal_db/daemon/install.py`:

```python
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

LABEL = "com.personal_db.daemon"
OLD_LABEL = "com.personal_db.scheduler"

# Module-level so tests can monkeypatch.
_LAUNCHAGENTS_DIR = Path("~/Library/LaunchAgents").expanduser()


def plist_path() -> Path:
    return _LAUNCHAGENTS_DIR / f"{LABEL}.plist"


def _old_plist_path() -> Path:
    return _LAUNCHAGENTS_DIR / f"{OLD_LABEL}.plist"


def build_plist(pdb_path: str, root: Path, log_path: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{pdb_path}</string>
    <string>--root</string><string>{root}</string>
    <string>daemon</string><string>run</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log_path}</string>
  <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""


def _migrate_old_plist() -> bool:
    """Unload + remove the old scheduler plist if present. Returns True if a migration happened."""
    old = _old_plist_path()
    if not old.exists():
        return False
    subprocess.run(["launchctl", "unload", str(old)], capture_output=True)
    old.unlink()
    return True


def install(root: Path) -> Path:
    pdb_path = shutil.which("personal-db") or "personal-db"
    log_path = root / "state" / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    migrated = _migrate_old_plist()

    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_plist(pdb_path, root, log_path))
    # Idempotent reload: unload first in case a previous version is loaded, then load fresh.
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    subprocess.run(["launchctl", "load", str(p)], check=True)
    return p


def uninstall() -> None:
    p = plist_path()
    if p.exists():
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        p.unlink()


def status() -> str:
    p = plist_path()
    if not p.exists():
        return "not installed"
    r = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True)
    if r.returncode != 0:
        return f"plist exists but not loaded: {p}"
    return r.stdout
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_install.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit.**

```bash
git add src/personal_db/daemon/install.py tests/unit/test_daemon_install.py
git commit -m "feat(daemon): plist generation + auto-migration of old scheduler"
```

---

## Task 3: Daemon HTTP client

**Goal:** A small client module that the CLI and MCP can call to delegate sync. Translates network failures into a single canonical exception.

**Files:**
- Create: `src/personal_db/daemon/client.py`
- Create: `tests/unit/test_daemon_client.py`

- [ ] **Step 1: Write the failing test.**

Create `tests/unit/test_daemon_client.py`:

```python
import socket
from unittest.mock import patch

import pytest
import requests

from personal_db.daemon import client as dc


def test_default_base_url_is_loopback_8765():
    assert dc.base_url() == "http://127.0.0.1:8765"


def test_base_url_respects_env(monkeypatch):
    monkeypatch.setenv("PERSONAL_DB_DAEMON_URL", "http://127.0.0.1:9000")
    assert dc.base_url() == "http://127.0.0.1:9000"


def test_sync_one_translates_connection_error():
    with patch.object(dc.requests, "post", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(dc.DaemonUnreachable) as ei:
            dc.sync_one("imessage")
        assert "daemon not running" in str(ei.value).lower()


def test_sync_one_returns_parsed_json_on_success():
    class FakeResp:
        status_code = 200
        def json(self):
            return {"ok": True, "tracker": "imessage"}
        def raise_for_status(self):
            return None
    with patch.object(dc.requests, "post", return_value=FakeResp()):
        out = dc.sync_one("imessage")
    assert out == {"ok": True, "tracker": "imessage"}


def test_sync_one_raises_daemon_error_on_5xx():
    class FakeResp:
        status_code = 500
        text = "boom"
        def raise_for_status(self):
            raise requests.HTTPError("500", response=self)
    with patch.object(dc.requests, "post", return_value=FakeResp()):
        with pytest.raises(dc.DaemonError):
            dc.sync_one("imessage")


def test_health_returns_dict_or_unreachable():
    with patch.object(dc.requests, "get", side_effect=requests.ConnectionError("nope")):
        with pytest.raises(dc.DaemonUnreachable):
            dc.health()
```

- [ ] **Step 2: Run to verify it fails.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_client.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement `daemon/client.py`.**

Create `src/personal_db/daemon/client.py`:

```python
"""HTTP client for the personal-db daemon. Used by CLI sync_cmd and MCP tools."""

from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_URL = "http://127.0.0.1:8765"
_TIMEOUT_SECONDS = 300  # generous; sync can take minutes for large backfills


class DaemonUnreachable(RuntimeError):
    """Raised when the daemon is not accepting connections.

    Callers should translate this into a directive user-facing message:
    `personal-db daemon not running. Run \`personal-db daemon install\``.
    """


class DaemonError(RuntimeError):
    """Raised when the daemon responds with a 5xx or otherwise-malformed reply."""


def base_url() -> str:
    return os.environ.get("PERSONAL_DB_DAEMON_URL", DEFAULT_URL)


def _post(path: str, params: dict | None = None) -> dict[str, Any]:
    url = f"{base_url()}{path}"
    try:
        resp = requests.post(url, params=params or {}, timeout=_TIMEOUT_SECONDS)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise DaemonUnreachable(
            f"daemon not running at {base_url()}: {e}"
        ) from e
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise DaemonError(f"daemon error: {resp.status_code} {resp.text[:200]}") from e
    return resp.json()


def _get(path: str) -> dict[str, Any]:
    url = f"{base_url()}{path}"
    try:
        resp = requests.get(url, timeout=10)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise DaemonUnreachable(
            f"daemon not running at {base_url()}: {e}"
        ) from e
    resp.raise_for_status()
    return resp.json()


def sync_one(name: str) -> dict[str, Any]:
    return _post(f"/api/sync/{name}")


def sync_due() -> dict[str, Any]:
    return _post("/api/sync_due")


def backfill(name: str, start: str | None, end: str | None) -> dict[str, Any]:
    params = {}
    if start:
        params["from"] = start
    if end:
        params["to"] = end
    return _post(f"/api/backfill/{name}", params=params)


def health() -> dict[str, Any]:
    return _get("/api/health")
```

- [ ] **Step 4: Run to verify it passes.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_client.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit.**

```bash
git add src/personal_db/daemon/client.py tests/unit/test_daemon_client.py
git commit -m "feat(daemon): HTTP client with DaemonUnreachable/DaemonError"
```

---

## Task 4: Add /api/sync/* routes to daemon/http.py

**Goal:** The daemon's FastAPI app gains four routes: `/api/sync/{tracker}`, `/api/sync_due`, `/api/backfill/{tracker}`, `/api/health`. Each routes to existing sync functions and serializes per-tracker writes via a lock.

**Files:**
- Modify: `src/personal_db/daemon/http.py`
- Create: `tests/unit/test_daemon_routes.py`

- [ ] **Step 1: Write the failing test.**

Create `tests/unit/test_daemon_routes.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_routes.py -q`
Expected: 404s on the new routes (or AttributeError if helpers don't exist yet).

- [ ] **Step 3: Add the routes to `src/personal_db/daemon/http.py`.**

Add these imports at the top of the file (alongside existing ones):

```python
import re
import threading
from collections import defaultdict
from typing import Any
```

Add after the existing imports, before `_HERE`:

```python
_TRACKER_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_tracker_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_DAEMON_START_TS: float | None = None


def _validate_name(name: str) -> None:
    if not _TRACKER_NAME_RE.match(name):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"invalid tracker name: {name!r}")
```

Inside `build_app(cfg)`, after the existing route registrations (just before `return app`), insert:

```python
    from personal_db.sync import backfill_one, sync_due, sync_one

    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        import time
        from personal_db.installer import list_bundled
        global _DAEMON_START_TS
        if _DAEMON_START_TS is None:
            _DAEMON_START_TS = time.time()
        installed = []
        if cfg.trackers_dir.exists():
            installed = sorted(d.name for d in cfg.trackers_dir.iterdir()
                               if d.is_dir() and (d / "manifest.yaml").exists())
        return {
            "status": "ok",
            "uptime_seconds": int(time.time() - _DAEMON_START_TS),
            "trackers": installed,
            "bundled_available": list_bundled(),
        }

    @app.post("/api/sync/{tracker}")
    async def api_sync_one(tracker: str) -> dict[str, Any]:
        _validate_name(tracker)
        if not (cfg.trackers_dir / tracker).is_dir():
            raise HTTPException(status_code=404, detail=f"no such tracker: {tracker}")
        with _tracker_locks[tracker]:
            try:
                sync_one(cfg, tracker)
            except Exception as e:  # noqa: BLE001 — surface to client
                raise HTTPException(status_code=500, detail=f"sync failed: {e}") from e
        return {"ok": True, "tracker": tracker}

    @app.post("/api/sync_due")
    async def api_sync_due() -> dict[str, Any]:
        results = sync_due(cfg)
        return {"results": results}

    @app.post("/api/backfill/{tracker}")
    async def api_backfill(tracker: str, request: Request) -> dict[str, Any]:
        _validate_name(tracker)
        if not (cfg.trackers_dir / tracker).is_dir():
            raise HTTPException(status_code=404, detail=f"no such tracker: {tracker}")
        start = request.query_params.get("from")
        end = request.query_params.get("to")
        with _tracker_locks[tracker]:
            try:
                backfill_one(cfg, tracker, start, end)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=f"backfill failed: {e}") from e
        return {"ok": True, "tracker": tracker, "from": start, "to": end}
```

(`HTTPException` and `Request` are already imported at the top of the file.)

- [ ] **Step 4: Run the test to verify it passes.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_routes.py -q`
Expected: 6 passed.

- [ ] **Step 5: Run the full suite.**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit.**

```bash
git add src/personal_db/daemon/http.py tests/unit/test_daemon_routes.py
git commit -m "feat(daemon): /api/sync/{tracker}, /api/sync_due, /api/backfill, /api/health"
```

---

## Task 5: Daemon orchestrator (server.py)

**Goal:** `daemon.server.run(cfg, port)` starts the periodic sync_due thread and runs uvicorn in the foreground. `start_periodic_sync(cfg, interval) -> Thread` is testable on its own.

**Files:**
- Create: `src/personal_db/daemon/server.py`
- Create: `tests/unit/test_daemon_server.py`

- [ ] **Step 1: Write the failing test.**

Create `tests/unit/test_daemon_server.py`:

```python
import threading
import time
from unittest.mock import patch

from personal_db.config import Config
from personal_db.daemon import server as ds


def test_start_periodic_sync_invokes_sync_due_repeatedly(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def fake_sync_due(c):
        calls.append(time.time())
        return {}

    stop = threading.Event()
    with patch("personal_db.daemon.server.sync_due", side_effect=fake_sync_due):
        thread = ds.start_periodic_sync(cfg, interval_seconds=0.05, stop_event=stop)
        time.sleep(0.18)  # enough for ~3 ticks
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, f"expected periodic ticks, got {len(calls)}"
    assert not thread.is_alive()


def test_start_periodic_sync_swallows_errors_and_continues(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    calls = []

    def flaky(c):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {}

    stop = threading.Event()
    with patch("personal_db.daemon.server.sync_due", side_effect=flaky):
        thread = ds.start_periodic_sync(cfg, interval_seconds=0.05, stop_event=stop)
        time.sleep(0.18)
        stop.set()
        thread.join(timeout=2.0)

    assert len(calls) >= 2, "thread should keep going after a single error"
```

- [ ] **Step 2: Run to verify it fails.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_server.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement `daemon/server.py`.**

Create `src/personal_db/daemon/server.py`:

```python
"""Daemon orchestrator: periodic sync loop + uvicorn server."""

from __future__ import annotations

import logging
import threading
import time

import uvicorn

from personal_db.config import Config
from personal_db.daemon.http import build_app
from personal_db.sync import sync_due

log = logging.getLogger("personal_db.daemon")


def start_periodic_sync(
    cfg: Config,
    interval_seconds: float = 600,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Spawn a daemon thread that runs sync_due every interval_seconds.

    Errors inside sync_due are caught and logged so a single tracker failure
    can't take down the loop.
    """
    stop = stop_event or threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                sync_due(cfg)
            except Exception:  # noqa: BLE001
                log.exception("periodic sync_due failed")
            # Wait in small slices so stop_event takes effect promptly.
            slept = 0.0
            while slept < interval_seconds and not stop.is_set():
                time.sleep(min(0.05, interval_seconds - slept))
                slept += 0.05

    t = threading.Thread(target=_loop, daemon=True, name="personal-db-periodic-sync")
    t.start()
    return t


def run(cfg: Config, port: int = 8765, interval_seconds: float = 600) -> None:
    """Run the daemon: start the periodic loop, then serve HTTP on 127.0.0.1:port."""
    start_periodic_sync(cfg, interval_seconds=interval_seconds)
    app = build_app(cfg)
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="info", access_log=False)
    uvicorn.Server(config).run()
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_server.py -q`
Expected: 2 passed.

- [ ] **Step 5: Commit.**

```bash
git add src/personal_db/daemon/server.py tests/unit/test_daemon_server.py
git commit -m "feat(daemon): orchestrator with periodic sync_due loop"
```

---

## Task 6: CLI `personal-db daemon` subcommands

**Goal:** New typer group `daemon` with `install`, `uninstall`, `status`, `restart`, `run`. `run` is the entrypoint launchd invokes; the rest manage the plist.

**Files:**
- Create: `src/personal_db/cli/daemon_cmd.py`
- Create: `tests/unit/test_daemon_cmd.py`

- [ ] **Step 1: Write the failing test.**

Create `tests/unit/test_daemon_cmd.py`:

```python
from typer.testing import CliRunner

import typer

from personal_db.cli import daemon_cmd
from personal_db.daemon import install as di


def _build_app() -> typer.Typer:
    app = typer.Typer()
    app.command("install")(daemon_cmd.install)
    app.command("uninstall")(daemon_cmd.uninstall)
    app.command("status")(daemon_cmd.status)
    app.command("restart")(daemon_cmd.restart)
    return app


def test_install_calls_install(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(di, "install", lambda root: (called.setdefault("root", root)) or (root / "p.plist"))
    monkeypatch.setattr("personal_db.cli.daemon_cmd.get_root", lambda: tmp_path)
    runner = CliRunner()
    r = runner.invoke(_build_app(), ["install"])
    assert r.exit_code == 0
    assert called["root"] == tmp_path
    assert "installed" in r.stdout.lower()


def test_uninstall_calls_uninstall(monkeypatch, tmp_path):
    called = {"yes": False}
    monkeypatch.setattr(di, "uninstall", lambda: called.update(yes=True))
    runner = CliRunner()
    r = runner.invoke(_build_app(), ["uninstall"])
    assert r.exit_code == 0
    assert called["yes"]


def test_status_prints_status(monkeypatch):
    monkeypatch.setattr(di, "status", lambda: "loaded\n")
    runner = CliRunner()
    r = runner.invoke(_build_app(), ["status"])
    assert r.exit_code == 0
    assert "loaded" in r.stdout
```

- [ ] **Step 2: Run to verify it fails.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_cmd.py -q`
Expected: ImportError.

- [ ] **Step 3: Implement `cli/daemon_cmd.py`.**

Create `src/personal_db/cli/daemon_cmd.py`:

```python
"""`personal-db daemon` — manage the long-running sync daemon."""

from __future__ import annotations

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.daemon import install as di


def install() -> None:
    """Install the launchd plist for the daemon and load it.

    Auto-migrates the old `com.personal_db.scheduler.plist` if present.
    """
    p = di.install(get_root())
    typer.echo(f"installed: {p}")


def uninstall() -> None:
    """Unload and remove the daemon plist."""
    di.uninstall()
    typer.echo("uninstalled")


def status() -> None:
    """Print launchctl's view of the daemon."""
    typer.echo(di.status())


def restart() -> None:
    """Reinstall the plist (unload + load). Equivalent to `uninstall && install`."""
    di.uninstall()
    p = di.install(get_root())
    typer.echo(f"restarted: {p}")


def run(
    port: int = typer.Option(8765, "--port"),
    interval_seconds: float = typer.Option(600, "--interval-seconds"),
) -> None:
    """Run the daemon in the foreground (called by launchd)."""
    from personal_db.daemon.server import run as _run

    cfg = Config(root=get_root())
    _run(cfg, port=port, interval_seconds=interval_seconds)
```

- [ ] **Step 4: Run the test to verify it passes.**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_cmd.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit.**

```bash
git add src/personal_db/cli/daemon_cmd.py tests/unit/test_daemon_cmd.py
git commit -m "feat(cli): personal-db daemon install/uninstall/status/restart/run"
```

---

## Task 7: Wire `daemon` into the main typer app; remove `scheduler` registration

**Goal:** `personal-db --help` lists `daemon` (with subcommands) and no longer lists `scheduler`. The `scheduler.py` module and `cli/scheduler_cmd.py` still exist on disk — they're removed in Task 14.

**Files:**
- Modify: `src/personal_db/cli/main.py`

- [ ] **Step 1: Edit `cli/main.py` to register the daemon group and drop scheduler.**

In `src/personal_db/cli/main.py`:

Replace the import block:
```python
from personal_db.cli import (
    init_cmd,
    log_cmd,
    mcp_cmd,
    permission_cmd,
    query_cmd,
    scheduler_cmd,
    setup_cmd,
    sync_cmd,
    tracker_cmd,
    ui_cmd,
)
```
with:
```python
from personal_db.cli import (
    daemon_cmd,
    init_cmd,
    log_cmd,
    mcp_cmd,
    permission_cmd,
    query_cmd,
    setup_cmd,
    sync_cmd,
    tracker_cmd,
    ui_cmd,
)
```

Replace:
```python
sched_app = typer.Typer(no_args_is_help=True, help="Background scheduler")
sched_app.command("install")(scheduler_cmd.install)
sched_app.command("uninstall")(scheduler_cmd.uninstall)
sched_app.command("status")(scheduler_cmd.status)
app.add_typer(sched_app, name="scheduler")
```
with:
```python
daemon_app = typer.Typer(no_args_is_help=True, help="Long-running sync daemon")
daemon_app.command("install")(daemon_cmd.install)
daemon_app.command("uninstall")(daemon_cmd.uninstall)
daemon_app.command("status")(daemon_cmd.status)
daemon_app.command("restart")(daemon_cmd.restart)
daemon_app.command("run")(daemon_cmd.run)
app.add_typer(daemon_app, name="daemon")
```

- [ ] **Step 2: Smoke test the CLI.**

Run: `.venv/bin/personal-db --help`
Expected: output includes `daemon` line, does NOT include `scheduler`.

Run: `.venv/bin/personal-db daemon --help`
Expected: lists `install`, `uninstall`, `status`, `restart`, `run`.

- [ ] **Step 3: Run the full suite.**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (the old `tests/unit/test_scheduler.py` still passes — `scheduler.py` still exists).

- [ ] **Step 4: Commit.**

```bash
git add src/personal_db/cli/main.py
git commit -m "feat(cli): register daemon typer group; drop scheduler"
```

---

## Task 8: Switch CLI sync/backfill to delegate via the daemon client

**Goal:** `personal-db sync <tracker>` and `personal-db backfill <tracker>` POST to the daemon. On `DaemonUnreachable`, exit 2 with the directive message. The in-process sync code path is gone from the CLI.

**Files:**
- Modify: `src/personal_db/cli/sync_cmd.py`
- Modify: `tests/unit/test_sync.py` (or a new file if it tests in-process behavior — see step 1)

- [ ] **Step 1: Find tests that exercise `cli.sync_cmd` directly.**

Run: `grep -rn "cli.sync_cmd\|from personal_db.cli import sync_cmd" tests/`
Expected: likely empty or minimal. The existing `tests/unit/test_sync.py` tests `personal_db.sync` (not the CLI); it should keep passing untouched. If there are CLI-level sync tests, they need updating in this task.

- [ ] **Step 2: Write the new failing test.**

Create `tests/unit/test_cli_sync_cmd.py`:

```python
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from personal_db.cli import sync_cmd
from personal_db.daemon import client as dc


def _app() -> typer.Typer:
    app = typer.Typer()
    app.command("sync")(sync_cmd.sync)
    app.command("backfill")(sync_cmd.backfill)
    return app


def test_sync_delegates_to_daemon():
    with patch.object(dc, "sync_one", return_value={"ok": True, "tracker": "demo"}) as m:
        r = CliRunner().invoke(_app(), ["sync", "demo"])
    assert r.exit_code == 0
    m.assert_called_once_with("demo")


def test_sync_due_delegates_to_daemon():
    with patch.object(dc, "sync_due", return_value={"results": {"a": "ok"}}) as m:
        r = CliRunner().invoke(_app(), ["sync", "--due"])
    assert r.exit_code == 0
    m.assert_called_once_with()


def test_sync_unreachable_exits_2_with_directive_message():
    with patch.object(dc, "sync_one", side_effect=dc.DaemonUnreachable("nope")):
        r = CliRunner().invoke(_app(), ["sync", "demo"])
    assert r.exit_code == 2
    assert "daemon install" in r.stderr.lower() or "daemon install" in r.stdout.lower()


def test_backfill_delegates_to_daemon():
    with patch.object(dc, "backfill", return_value={"ok": True}) as m:
        r = CliRunner().invoke(_app(), ["backfill", "demo", "--from", "2026-01-01", "--to", "2026-01-02"])
    assert r.exit_code == 0
    m.assert_called_once_with("demo", "2026-01-01", "2026-01-02")


def test_backfill_unreachable_exits_2():
    with patch.object(dc, "backfill", side_effect=dc.DaemonUnreachable("nope")):
        r = CliRunner().invoke(_app(), ["backfill", "demo"])
    assert r.exit_code == 2
```

- [ ] **Step 3: Run to verify it fails.**

Run: `.venv/bin/python -m pytest tests/unit/test_cli_sync_cmd.py -q`
Expected: failures (typer's CliRunner needs `mix_stderr=False` for stderr assertion, but test passes if exit code is right and message appears in either stream).

- [ ] **Step 4: Rewrite `cli/sync_cmd.py`.**

Replace the entire contents of `src/personal_db/cli/sync_cmd.py` with:

```python
import typer

from personal_db.daemon import client as dc

_DAEMON_HINT = "personal-db daemon not running. Run `personal-db daemon install`"


def sync(
    name: str = typer.Argument(None),
    due: bool = typer.Option(False, "--due", help="Run only trackers that are due"),
) -> None:
    """Run sync for a tracker, or all due trackers (delegates to daemon)."""
    try:
        if due:
            out = dc.sync_due()
            for n, status in out.get("results", {}).items():
                typer.echo(f"  {n}: {status}")
        elif name:
            dc.sync_one(name)
            typer.echo(f"synced {name}")
        else:
            typer.echo("specify a tracker name or --due", err=True)
            raise typer.Exit(2)
    except dc.DaemonUnreachable:
        typer.echo(_DAEMON_HINT, err=True)
        raise typer.Exit(2) from None
    except dc.DaemonError as e:
        typer.echo(f"daemon error: {e}", err=True)
        raise typer.Exit(1) from None


def backfill(
    name: str = typer.Argument(...),
    from_: str = typer.Option(None, "--from"),
    to: str = typer.Option(None, "--to"),
) -> None:
    """Backfill a tracker over a date range (delegates to daemon)."""
    try:
        dc.backfill(name, from_, to)
        typer.echo(f"backfilled {name}")
    except dc.DaemonUnreachable:
        typer.echo(_DAEMON_HINT, err=True)
        raise typer.Exit(2) from None
    except dc.DaemonError as e:
        typer.echo(f"daemon error: {e}", err=True)
        raise typer.Exit(1) from None
```

- [ ] **Step 5: Run the new test.**

Run: `.venv/bin/python -m pytest tests/unit/test_cli_sync_cmd.py -q`
Expected: 5 passed.

- [ ] **Step 6: Run the full suite.**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit.**

```bash
git add src/personal_db/cli/sync_cmd.py tests/unit/test_cli_sync_cmd.py
git commit -m "feat(cli): sync/backfill delegate to daemon, hard-fail when unreachable"
```

---

## Task 9: Switch MCP sync tools to delegate via the daemon client

**Goal:** MCP `sync_tool`, `sync_due_tool`, `backfill_tool` delegate to the daemon. The MCP server's call_tool dispatch wraps these in `asyncio.to_thread` so the asyncio event loop isn't blocked by the synchronous `requests` call. On `DaemonUnreachable`, the tool returns a structured error payload (so the agent sees the directive message instead of an exception trace).

**Files:**
- Modify: `src/personal_db/mcp_server/tools.py:272-300` (sync_tool, sync_due_tool, backfill_tool)
- Modify: `src/personal_db/mcp_server/server.py:260-270` (wrap dispatches in to_thread)
- Modify: `tests/unit/test_mcp_tools.py` (update sync/sync_due/backfill tests)

- [ ] **Step 1: Update the MCP tool tests.**

Edit `tests/unit/test_mcp_tools.py`:

1. Add to the imports at the top of the file:
   ```python
   from unittest.mock import patch

   from personal_db.daemon import client as dc
   ```
2. Delete these existing test functions: `test_sync_tool_runs_and_records_last_run`, `test_sync_tool_rejects_unknown_tracker`, `test_sync_tool_rejects_invalid_name`, `test_sync_due_tool_runs_pending`, `test_backfill_tool_invokes_ingest_backfill`.
3. Append in their place:

```python


def test_sync_tool_delegates_to_daemon(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "sync_one", return_value={"ok": True, "tracker": "runnable"}) as m:
        out = sync_tool(cfg, "runnable")
    m.assert_called_once_with("runnable")
    assert out == {"ok": True, "tracker": "runnable"}


def test_sync_tool_returns_structured_error_on_unreachable(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "sync_one", side_effect=dc.DaemonUnreachable("nope")):
        out = sync_tool(cfg, "runnable")
    assert out["ok"] is False
    assert "daemon" in out["error"].lower()


def test_sync_due_tool_delegates(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "sync_due", return_value={"results": {"runnable": "ok"}}) as m:
        out = sync_due_tool(cfg)
    m.assert_called_once_with()
    assert out["results"]["runnable"] == "ok"


def test_sync_due_tool_unreachable(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "sync_due", side_effect=dc.DaemonUnreachable("nope")):
        out = sync_due_tool(cfg)
    assert out["ok"] is False
    assert "daemon" in out["error"].lower()


def test_backfill_tool_delegates(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "backfill", return_value={"ok": True}) as m:
        out = backfill_tool(cfg, "runnable", "2026-04-01", "2026-04-02")
    m.assert_called_once_with("runnable", "2026-04-01", "2026-04-02")
    assert out["ok"] is True


def test_backfill_tool_unreachable(tmp_root):
    cfg = _make_runnable_tracker(tmp_root)
    with patch.object(dc, "backfill", side_effect=dc.DaemonUnreachable("nope")):
        out = backfill_tool(cfg, "runnable")
    assert out["ok"] is False
```

- [ ] **Step 2: Run to verify the rewritten tests fail.**

Run: `.venv/bin/python -m pytest tests/unit/test_mcp_tools.py -q -k "sync or backfill"`
Expected: failures — sync_tool still calls sync_one in-process.

- [ ] **Step 3: Rewrite sync_tool, sync_due_tool, backfill_tool in `mcp_server/tools.py`.**

Replace lines 272-300 of `src/personal_db/mcp_server/tools.py` with:

```python
def sync_tool(cfg: Config, name: str) -> dict[str, Any]:
    if not _TRACKER_NAME_RE.match(name):
        return {"ok": False, "error": f"invalid tracker name: {name!r}"}
    if not (cfg.trackers_dir / name).is_dir():
        return {"ok": False, "error": f"no such tracker: {name}"}
    from personal_db.daemon import client as dc
    try:
        return dc.sync_one(name)
    except dc.DaemonUnreachable as e:
        return {
            "ok": False,
            "error": f"personal-db daemon not running. Run `personal-db daemon install`. ({e})",
        }
    except dc.DaemonError as e:
        return {"ok": False, "error": f"daemon error: {e}"}


def sync_due_tool(cfg: Config) -> dict[str, Any]:  # noqa: ARG001 — cfg kept for API parity
    from personal_db.daemon import client as dc
    try:
        return dc.sync_due()
    except dc.DaemonUnreachable as e:
        return {
            "ok": False,
            "error": f"personal-db daemon not running. Run `personal-db daemon install`. ({e})",
        }
    except dc.DaemonError as e:
        return {"ok": False, "error": f"daemon error: {e}"}


def backfill_tool(
    cfg: Config,
    name: str,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    if not _TRACKER_NAME_RE.match(name):
        return {"ok": False, "error": f"invalid tracker name: {name!r}"}
    if not (cfg.trackers_dir / name).is_dir():
        return {"ok": False, "error": f"no such tracker: {name}"}
    from personal_db.daemon import client as dc
    try:
        return dc.backfill(name, start, end)
    except dc.DaemonUnreachable as e:
        return {
            "ok": False,
            "error": f"personal-db daemon not running. Run `personal-db daemon install`. ({e})",
        }
    except dc.DaemonError as e:
        return {"ok": False, "error": f"daemon error: {e}"}
```

Also remove the now-unused `sync_one` and `sync_due` imports from `mcp_server/tools.py` (search for `from personal_db.sync import` and trim). And remove `_read_last_run` if it's only used by the old sync_tool — verify with grep first.

- [ ] **Step 4: Wrap MCP dispatches in `asyncio.to_thread`.**

In `src/personal_db/mcp_server/server.py`, add at the top:

```python
import asyncio
```

In the `_call` function (lines ~260-270), change the three branches:
```python
        elif name == "sync":
            result = T.sync_tool(cfg, arguments["name"])
        elif name == "sync_due":
            result = T.sync_due_tool(cfg)
        elif name == "backfill":
            result = T.backfill_tool(
                cfg,
                arguments["name"],
                arguments.get("from"),
                arguments.get("to"),
            )
```
to:
```python
        elif name == "sync":
            result = await asyncio.to_thread(T.sync_tool, cfg, arguments["name"])
        elif name == "sync_due":
            result = await asyncio.to_thread(T.sync_due_tool, cfg)
        elif name == "backfill":
            result = await asyncio.to_thread(
                T.backfill_tool,
                cfg,
                arguments["name"],
                arguments.get("from"),
                arguments.get("to"),
            )
```

- [ ] **Step 5: Run the MCP tool tests.**

Run: `.venv/bin/python -m pytest tests/unit/test_mcp_tools.py -q`
Expected: all pass.

- [ ] **Step 6: Run the full suite.**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit.**

```bash
git add src/personal_db/mcp_server/tools.py src/personal_db/mcp_server/server.py tests/unit/test_mcp_tools.py
git commit -m "feat(mcp): sync/sync_due/backfill delegate to daemon via to_thread"
```

---

## Task 10: Update menubar UI — drop in-process dashboard, "Force sync" via daemon

**Goal:** `personal-db ui` no longer starts its own FastAPI server (the daemon hosts the dashboard now). The "Force sync" button calls the daemon over HTTP. "Open dashboard" still opens `http://127.0.0.1:8765/`.

**Files:**
- Modify: `src/personal_db/ui/menubar.py`

- [ ] **Step 1: Edit `ui/menubar.py`.**

In `src/personal_db/ui/menubar.py`:

Remove these imports (no longer needed):
```python
import uvicorn
from personal_db.daemon.http import build_app
from personal_db.sync import sync_due
```

Add:
```python
from personal_db.daemon import client as dc
```

Delete the `_start_server` function (lines ~41-54) entirely.

Replace the `_sync_all` method body:
```python
    def _sync_all(self, _) -> None:
        # Run in a thread so the menu bar stays responsive during sync.
        def run():
            try:
                results = dc.sync_due().get("results", {})
                ok = sum(1 for v in results.values() if v == "ok")
                err = sum(1 for v in results.values() if v.startswith("error"))
                msg = f"{ok} synced · {err} errored"
                rumps.notification("personal_db", "sync done", msg, sound=False)
            except dc.DaemonUnreachable:
                rumps.notification(
                    "personal_db",
                    "daemon not running",
                    "Run `personal-db daemon install`",
                    sound=False,
                )
            except Exception as e:  # noqa: BLE001
                rumps.notification("personal_db", "sync failed", str(e), sound=False)
            self._refresh()
        threading.Thread(target=run, daemon=True).start()
```

Replace the bottom `run_menubar` function:
```python
def run_menubar(cfg: Config, port: int = 8765) -> None:
    """Run the rumps menubar. The dashboard is served by the daemon
    at http://127.0.0.1:<port>/, NOT by this process."""
    PersonalDBApp(cfg, port).run()
```

- [ ] **Step 2: Run UI-adjacent tests.**

Run: `.venv/bin/python -m pytest tests/unit/test_ui_setup.py tests/unit/test_ui_viz.py -q`
Expected: pass.

- [ ] **Step 3: Smoke test the import.**

Run: `.venv/bin/python -c "from personal_db.ui.menubar import run_menubar; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit.**

```bash
git add src/personal_db/ui/menubar.py
git commit -m "refactor(ui): menubar drops in-process dashboard, calls daemon for sync"
```

---

## Task 11: Simplify `personal-db ui` — drop --no-menubar

**Goal:** `personal-db ui` is now just the rumps shell. Headless dashboard = just don't run `ui`; the dashboard is always at the daemon's URL.

**Files:**
- Modify: `src/personal_db/cli/ui_cmd.py`

- [ ] **Step 1: Replace `cli/ui_cmd.py`.**

```python
"""`personal-db ui` — launch the menubar shell. The dashboard is served by the daemon."""

from __future__ import annotations

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config


def ui(
    port: int = typer.Option(8765, "--port", help="Daemon dashboard port (for the 'Open dashboard' menu item)"),
) -> None:
    """Launch the menubar shell. The dashboard runs in the daemon at
    http://127.0.0.1:<port>/ — make sure `personal-db daemon install` was run."""
    from personal_db.ui.menubar import run_menubar

    cfg = Config(root=get_root())
    run_menubar(cfg, port=port)
```

- [ ] **Step 2: Smoke test.**

Run: `.venv/bin/personal-db ui --help`
Expected: shows `--port` only (no `--no-menubar`).

- [ ] **Step 3: Run the full suite.**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit.**

```bash
git add src/personal_db/cli/ui_cmd.py
git commit -m "refactor(cli): personal-db ui drops --no-menubar (dashboard moved to daemon)"
```

---

## Task 12: Update setup wizard finalize to install the daemon

**Goal:** The web setup wizard's "finish" page installs the daemon (was: scheduler).

**Files:**
- Modify: `src/personal_db/daemon/http.py` (the `_install_scheduler_safe` function relocated here in Task 1)
- Modify: `tests/unit/test_ui_setup.py` if it asserts on the scheduler message

- [ ] **Step 1: Find usages of the helper.**

Run: `grep -rn "_install_scheduler_safe\|PERSONAL_DB_NO_SCHEDULER" src/ tests/`
Note where the helper is referenced.

- [ ] **Step 2: Rename and rewire.**

In `src/personal_db/daemon/http.py`, find `_install_scheduler_safe` and rename it to `_install_daemon_safe`. Replace its body so it calls `daemon.install.install` instead of `scheduler.install`:

```python
def _install_daemon_safe(cfg: Config) -> str:
    """Install the launchd daemon plist. Returns a one-line status string for the
    finalize page. Idempotent. macOS-only.

    Honors PERSONAL_DB_NO_DAEMON=1 (and the deprecated PERSONAL_DB_NO_SCHEDULER=1)
    so tests/demos can opt out of clobbering the user's real install."""
    import os

    if os.environ.get("PERSONAL_DB_NO_DAEMON") == "1" or os.environ.get("PERSONAL_DB_NO_SCHEDULER") == "1":
        return "✓ daemon skipped (PERSONAL_DB_NO_DAEMON=1)"
    if sys.platform != "darwin":
        return f"⚠ daemon is macOS-only (detected {sys.platform}); periodic sync skipped"
    try:
        from personal_db.daemon import install as di

        plist = di.install(cfg.root)
        return f"✓ daemon installed → {plist} (long-running, KeepAlive)"
    except Exception as e:  # noqa: BLE001
        return f"⚠ daemon install failed: {e}"
```

Inside the same file, find the call site `scheduler_msg = _install_scheduler_safe(cfg)` and change to `scheduler_msg = _install_daemon_safe(cfg)` (the variable name can stay as `scheduler_msg` for the template, or rename to `daemon_msg` if templates are also updated — check).

- [ ] **Step 3: Update template variable if needed.**

Run: `grep -rn "scheduler_msg\|daemon_msg" src/personal_db/ui/templates/`
If the template uses `scheduler_msg`, either keep that variable name OR rename consistently. Picking minimum diff: keep `scheduler_msg` as the template variable name; just change what it contains.

- [ ] **Step 4: Run UI setup tests.**

Run: `.venv/bin/python -m pytest tests/unit/test_ui_setup.py -q`
Expected: pass. If a test asserts on the literal string "scheduler installed", update it.

- [ ] **Step 5: Commit.**

```bash
git add src/personal_db/daemon/http.py tests/unit/test_ui_setup.py
git commit -m "feat(setup): wizard finalize installs daemon (renamed env: PERSONAL_DB_NO_DAEMON)"
```

---

## Task 13: Remove old scheduler module + CLI command + test

**Goal:** Delete the now-unused `scheduler.py`, `cli/scheduler_cmd.py`, and `tests/unit/test_scheduler.py`. Verify nothing references them.

**Files:**
- Delete: `src/personal_db/scheduler.py`
- Delete: `src/personal_db/cli/scheduler_cmd.py`
- Delete: `tests/unit/test_scheduler.py`

- [ ] **Step 1: Verify no remaining references.**

Run: `grep -rn "from personal_db.scheduler\|from personal_db.cli.scheduler_cmd\|import scheduler_cmd" src/ tests/`
Expected: empty (both modules should be unreferenced).

If anything turns up, fix it before deleting.

- [ ] **Step 2: Delete the files.**

```bash
git rm src/personal_db/scheduler.py src/personal_db/cli/scheduler_cmd.py tests/unit/test_scheduler.py
```

- [ ] **Step 3: Run the full suite.**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 4: Smoke test the CLI surface.**

Run: `.venv/bin/personal-db --help`
Expected: `daemon` listed; no `scheduler`.

Run: `.venv/bin/python -c "import personal_db.scheduler" 2>&1 | head -1`
Expected: `ModuleNotFoundError: No module named 'personal_db.scheduler'`.

- [ ] **Step 5: Commit.**

```bash
git commit -m "chore: remove obsolete scheduler module + CLI + test"
```

---

## Task 14: Update CLAUDE.md and the design's living references

**Goal:** Update the project's CLAUDE.md to mention the daemon (it currently has no scheduler/daemon section but should). Add a one-liner to the "Useful one-liners" block.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Read current CLAUDE.md.**

Run: `cat CLAUDE.md`

- [ ] **Step 2: Add a daemon section.**

Append after the "Where things live" section:

```markdown
## The sync daemon

`personal-db sync <tracker>` and the MCP `sync` tool both delegate to a long-running daemon at `http://127.0.0.1:8765`. The daemon is launchd-managed (`com.personal_db.daemon`) and holds the macOS Full Disk Access grant via the python interpreter, so sync works regardless of which process triggered it.

```bash
# install (auto-migrates from the old scheduler plist)
personal-db daemon install

# check status
personal-db daemon status

# manually run in foreground (debugging)
personal-db daemon run --port 8766
```

If sync prints `personal-db daemon not running`, the fix is `personal-db daemon install`.
```

- [ ] **Step 3: Commit.**

```bash
git add CLAUDE.md
git commit -m "docs: add sync daemon section to CLAUDE.md"
```

---

## Task 15: End-to-end manual verification on the live system

**Goal:** Confirm that, after merging, the live `~/personal_db` install works correctly: migration completes, sync works from a non-FDA shell, the dashboard is reachable, the menubar still works.

This task does not produce code changes; it verifies the implementation against reality.

- [ ] **Step 1: Migrate the live install.**

Run: `personal-db daemon install`

Expected output: a line about migrating the old scheduler plist (if present), then `installed: ~/Library/LaunchAgents/com.personal_db.daemon.plist`.

Verify: `launchctl list | grep personal_db` shows `com.personal_db.daemon` (not `com.personal_db.scheduler`).

Verify: `~/Library/LaunchAgents/com.personal_db.scheduler.plist` no longer exists.

- [ ] **Step 2: Confirm daemon is reachable.**

Run: `curl -s http://127.0.0.1:8765/api/health | jq`

Expected: JSON with `status: "ok"`, `uptime_seconds`, `trackers: [...]`.

- [ ] **Step 3: Sync from a fresh shell (no inherited FDA).**

Run: `personal-db sync claude_sessions` (a non-FDA tracker)

Expected: `synced claude_sessions`.

Run: `personal-db sync imessage` (FDA-gated)

Expected: `synced imessage` — it works because the daemon, not the shell, reads `chat.db`.

- [ ] **Step 4: Confirm hard-fail when daemon is down.**

Run: `launchctl unload ~/Library/LaunchAgents/com.personal_db.daemon.plist`

Run: `personal-db sync claude_sessions`

Expected: stderr `personal-db daemon not running. Run \`personal-db daemon install\``, exit code 2.

Re-load: `launchctl load ~/Library/LaunchAgents/com.personal_db.daemon.plist`

- [ ] **Step 5: Confirm the dashboard.**

Open `http://127.0.0.1:8765/` in a browser.

Expected: the dashboard renders normally (it now lives in the daemon).

- [ ] **Step 6: Confirm the menubar.**

Run: `personal-db ui`

Expected: menubar appears. Click "Force sync" → notification "X synced · Y errored".

Click "Open dashboard" → browser opens `http://127.0.0.1:8765/`. Quit the menubar.

Verify: `http://127.0.0.1:8765/` is STILL reachable after quitting the menubar (because the daemon owns it now).

- [ ] **Step 7: Confirm MCP sync works from Claude Code.**

In a fresh Claude Code session: ask the agent to call the `sync` MCP tool for `claude_sessions`.

Expected: success — the agent's process never had FDA, but it doesn't need to.

---

## Self-review notes (filled in during plan write)

- **Spec coverage:** All sections of the design doc map to a task — Architecture (Tasks 4–6), Files / new (Tasks 1–6), Files / modified (Tasks 7–11), Files / removed (Task 13), Data flow (Tasks 4, 8, 9), HTTP API (Task 4), Sub-decisions (port and bind in Task 5, lock granularity in Task 4, route prefix in Task 4, log filename in Task 2), Error handling (Tasks 8, 9), Migration (Task 2 builds it, Task 15 verifies live), Testing (TDD inside each task, Task 15 manual).
- **Placeholder scan:** No "TBD" / "TODO" / "implement later" markers. Every step shows the actual code or command.
- **Type consistency:** `DaemonUnreachable` and `DaemonError` are defined in Task 3 and referenced in Tasks 8, 9, 10. `LABEL`/`OLD_LABEL`/`_LAUNCHAGENTS_DIR` defined in Task 2 are referenced in Task 6's CLI tests via `personal_db.daemon.install`. `build_app(cfg)` API preserved across the move in Task 1 and extended in Task 4. `start_periodic_sync` signature in Task 5 matches its test usage.
- **Scope:** One feature, one daemon, one PR's worth of changes. Not decomposable further.
