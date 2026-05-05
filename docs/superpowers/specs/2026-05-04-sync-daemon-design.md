# Sync daemon — design

**Date:** 2026-05-04
**Status:** Approved, pending implementation plan
**Branch:** `feat/granola-tracker` (design only; implementation will land on a new branch)

## Problem

`personal-db sync` for FDA-gated trackers (currently `imessage`, `screen_time`) only works when invoked from a parent process that has macOS Full Disk Access. Terminal.app typically does; agents (Claude Code, Cursor) and ad-hoc scheduled jobs typically don't. The result: the same `personal-db` binary works fine in one shell and fails with "operation not permitted" in another, because TCC attribution is parent-process-dependent.

The query path doesn't have this problem — `~/personal_db/db.sqlite` lives in the user's own home directory and needs no special grants. So the asymmetry is: any caller can read; only privileged callers can write.

We want sync to "just work" from anywhere — CLI, MCP, scheduled jobs — without each caller having to be individually granted FDA.

## Solution overview

Introduce a long-running `personal-db daemon` process under launchd. It holds the FDA grant (via the python interpreter) and is the single place sync ever runs. CLI and MCP `sync` calls become thin HTTP clients that POST to it. The existing FastAPI dashboard moves into the daemon so there is one always-on local server instead of two competing ones.

If the daemon isn't running, `sync` hard-fails with a directive error message — there is no in-process fallback. This keeps the mental model crisp: "sync only happens in one place."

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  launchd (macOS init, always running)                       │
│   └─ KeepAlive=true, RunAtLoad=true                         │
│      └─ personal-db daemon run                              │
│         ├─ FastAPI on 127.0.0.1:8765                        │
│         │   ├─ Dashboard routes (/, /v/*, /t/*, /setup/*)   │
│         │   └─ Sync API (/api/sync/*, /api/backfill/*)      │
│         └─ Background thread: periodic sync_due loop        │
└─────────────────────────────────────────────────────────────┘
            ▲                    ▲                    ▲
            │ HTTP               │ HTTP               │ HTTP
   ┌────────┴────────┐  ┌────────┴────────┐  ┌────────┴────────┐
   │ personal-db CLI │  │ MCP server      │  │ menubar (rumps) │
   │  (sync, etc.)   │  │ (sync_tool)     │  │  ("Force sync") │
   └─────────────────┘  └─────────────────┘  └─────────────────┘
```

FDA is granted once to the python interpreter resolved by `responsible_binary_path()`. Every protected file read happens inside the daemon, which inherits that grant via the binary identity regardless of who triggers a sync request.

## Files

### New

- **`src/personal_db/daemon/__init__.py`** — empty package marker.
- **`src/personal_db/daemon/server.py`** — orchestrator. `run(cfg, port)` builds the FastAPI app and starts the periodic sync_due thread. This is what `personal-db daemon run` calls.
- **`src/personal_db/daemon/http.py`** — moved from `src/personal_db/ui/server.py`. Same `build_app(cfg)` API, with new `/api/sync/*` routes added.
- **`src/personal_db/daemon/client.py`** — HTTP client wrapper. Exposes `sync_one(name) -> dict`, `sync_due() -> dict`, `backfill(name, from_, to) -> dict`. Connection refused / timeout → raises `DaemonUnreachable` with a canonical message.
- **`src/personal_db/daemon/install.py`** — replaces `scheduler.py`. Plist generation, install/uninstall/status, **auto-migration** of old `com.personal_db.scheduler.plist`.
- **`src/personal_db/cli/daemon_cmd.py`** — typer subcommands: `install`, `uninstall`, `status`, `restart`, `run`. `run` is the entrypoint launchd invokes; the rest are user-facing management.

### Modified

- **`src/personal_db/cli/sync_cmd.py`** — `sync` and `backfill` delegate to `daemon.client`. On `DaemonUnreachable`: print directive error to stderr, exit 2.
- **`src/personal_db/mcp_server/tools.py`** — `sync_tool`, `backfill_tool`, `sync_due_tool` delegate to `daemon.client`. On `DaemonUnreachable`: return structured error in the tool response.
- **`src/personal_db/ui/menubar.py`** — drop `_start_server` and the daemon-thread that runs it. "Force sync" button calls `daemon.client.sync_due()`. "Open dashboard" still opens `http://127.0.0.1:8765/` (now served by the daemon).
- **`src/personal_db/cli/ui_cmd.py`** — `personal-db ui` becomes "menubar shell only". Drop `--no-menubar` (headless = just don't run `ui`; the dashboard is always reachable at the daemon's URL).
- **`src/personal_db/cli/main.py`** — register `daemon` typer group, remove `scheduler`.
- **`src/personal_db/ui/server.py`** → file relocates to `src/personal_db/daemon/http.py`. The `_install_scheduler_safe(cfg)` helper inside it is renamed to `_install_daemon_safe(cfg)` and calls `daemon.install.install()`.

### Removed

- `src/personal_db/cli/scheduler_cmd.py` — replaced by `cli/daemon_cmd.py`.
- `src/personal_db/scheduler.py` — replaced by `daemon/install.py`.
- `src/personal_db/ui/server.py` — relocated to `daemon/http.py` (the file is gone from its old path; the `build_app(cfg)` symbol moves with it).

## Data flow

### CLI sync

```
$ personal-db sync imessage
  └─ cli/sync_cmd.py:sync()
     └─ daemon.client.sync_one("imessage")
        └─ POST http://127.0.0.1:8765/api/sync/imessage
           └─ daemon route handler:
              ├─ acquire per-tracker lock
              ├─ personal_db.sync.sync_one(cfg, "imessage")
              └─ return JSON {"status": "ok", "rows_added": N}
        └─ raises DaemonUnreachable on connection refused/timeout
     └─ on unreachable: print "personal-db daemon not running. Run `personal-db daemon install`" → exit 2
     └─ on success: print "synced imessage" → exit 0
     └─ on sync error from daemon: print error → exit 1
```

### MCP sync (from Claude Code)

Identical to CLI flow except the result and any error are returned in the MCP tool response so the agent can surface them to the user.

### Periodic loop

A background thread inside the daemon calls `sync_due(cfg)` every `interval_seconds` (default 600). Same `sync_due` function as today; it's just running in-process instead of being launchd-forked.

## HTTP API

All under `/api/` prefix to coexist cleanly with the dashboard's `/v/`, `/t/`, `/setup/` routes.

| Method | Path                          | Body / Query             | Returns |
|--------|-------------------------------|--------------------------|---------|
| POST   | `/api/sync/{tracker}`         | —                        | `{"status": "ok", "rows_added": N}` or `{"status": "error", "detail": "..."}` |
| POST   | `/api/sync_due`               | —                        | `{"results": {"tracker_a": "ok", "tracker_b": "error: ..."}}` |
| POST   | `/api/backfill/{tracker}`     | `?from=...&to=...`       | `{"status": "ok"}` or error |
| GET    | `/api/health`                 | —                        | `{"status": "ok", "uptime_seconds": N, "trackers": [...]}` |

## Sub-decisions

- **Port: 8765** — same as today's dashboard. The dashboard is moving processes, not changing URL. Existing browser bookmarks keep working.
- **Bind: 127.0.0.1 only.** Loopback isolation matches the macOS user trust boundary. Anything on the user's machine can already read `~/personal_db/db.sqlite` directly.
- **No token auth.** Loopback-only + per-user file permissions on the data dir is the existing trust model. Adding tokens would be over-engineering for this threat model.
- **Per-tracker `threading.Lock`** inside the daemon. SQLite serializes writes already, but a lock prevents two concurrent sync requests for the same tracker from re-fetching the same external rows or fighting over cursor state.
- **`/api/` prefix** for all sync routes to keep the dashboard's URL space clean.
- **Logs: `state/daemon.log`** (renamed from `state/scheduler.log` during migration).

## Error handling

- **Daemon unreachable (CLI):** `cli/sync_cmd.py` catches `DaemonUnreachable`, prints `personal-db daemon not running. Run \`personal-db daemon install\`` to stderr, exits 2.
- **Daemon unreachable (MCP):** `mcp_server/tools.py` catches `DaemonUnreachable`, returns a structured error in the tool response payload.
- **Daemon up, sync errors:** forwarded from `personal_db.sync.sync_one`'s exception → JSON error response → CLI exits 1 / MCP returns error. Existing `state/sync_errors.jsonl` continues to be written by `sync_one` itself.
- **Daemon process crash:** launchd's `KeepAlive` respawns it. Stderr/stdout go to `state/daemon.log`.
- **Concurrent sync requests for same tracker:** per-tracker lock blocks the second request until the first completes; the second then runs (will likely no-op since the first just synced).
- **Setup wizard:** `setup_finish` route calls `daemon.install.install()` instead of today's `_install_scheduler_safe()`. Honors `PERSONAL_DB_NO_SCHEDULER=1` (renamed env var: `PERSONAL_DB_NO_DAEMON=1`, with the old name still recognized for one release as a deprecation alias).

## Migration

`personal-db daemon install` performs:

1. Detect old plist at `~/Library/LaunchAgents/com.personal_db.scheduler.plist`.
   - If present: `launchctl unload <path>`, `unlink <path>`. Print `migrating from old scheduler...`.
2. Write `com.personal_db.daemon.plist` with:
   - `Label = com.personal_db.daemon`
   - `KeepAlive = true`
   - `RunAtLoad = true`
   - `ProgramArguments = [<personal-db-path>, --root, <root>, daemon, run]`
   - `StandardOutPath / StandardErrorPath = <root>/state/daemon.log`
3. `launchctl load <new-path>`.
4. Print `✓ daemon installed at <path>; old scheduler removed (if present)`.

No manual cleanup required from the user; one command handles it.

## Testing

### Unit
- `daemon/client.py`: connection refused → `DaemonUnreachable` with the canonical message; HTTP 5xx → `DaemonError` with detail; HTTP 200 → parsed dict.
- `cli/sync_cmd.py`: `DaemonUnreachable` → exit code 2 + expected stderr message.
- `mcp_server/tools.py`: `DaemonUnreachable` → structured error in tool response.
- `daemon/install.py`: when old `com.personal_db.scheduler.plist` is present (use a tmp `LaunchAgents` dir via env or arg), it gets unloaded + deleted before new plist is written.
- `daemon/install.py`: generated plist XML matches expected ProgramArguments and KeepAlive=true.

### Integration
- Spin up the daemon on a test port via `daemon.server.run(cfg, port=test_port)` in a thread; POST `/api/sync/<tracker>` for a tracker with a deterministic fixture; assert row count changed.
- Mock the periodic loop's timer; assert `sync_due` is invoked at the expected cadence.
- Health endpoint returns expected shape.

### Manual
- `personal-db daemon run --port 8767` in foreground; from another terminal `personal-db sync claude_sessions` → verify it goes through and exits 0.
- Stop the daemon; rerun `personal-db sync claude_sessions` → verify exit 2 + correct stderr.
- On the live system: run `personal-db daemon install` with the existing `com.personal_db.scheduler.plist` present → verify migration completes, old plist gone, new plist loaded, `personal-db sync imessage` works from a non-FDA shell.

## Out of scope (deliberately YAGNI)

- Token auth or HTTPS on the daemon's HTTP port.
- Multi-user / multi-root daemon (one daemon per user, one root per daemon).
- Linux/Windows daemon equivalents (current scheduler is macOS-only; same constraint carries over).
- An in-process fallback when the daemon isn't running (rejected as F1 above — the directive error message is the design).
- A separate "syncd" process distinct from the dashboard server (rejected as B1 above — one daemon owns both).

## Open questions for review

None at this point — all foundational decisions (B / B2 / F1 / C1) and sub-decisions (port, auth, lock granularity, route prefix, log filename) are settled.
