# Code-agent activity tracker — design

**Date:** 2026-05-09
**Status:** Approved, pending implementation plan
**Branch:** TBD (design only)

## Problem

There is no good local record of how much time AI coding agents (Claude Code, Codex CLI) actually spend running, idling, or waiting for the user — and no way to correlate that against user attention. The user wants to answer:

1. **Efficiency** — how much wall-clock time did agents spend running vs idle? When the agent was running, was the user productive elsewhere or waiting on it?
2. **Engagement** — what fraction of agent runtime was the user actively engaged (typing, reading) vs the agent ran while the user was away or distracted?
3. **Cadence** — what is the rhythm of prompts? How long are gaps, how do they cluster, when is the user most active with agents?

Multiple sessions can be running concurrently across both agents; each must be tracked separately.

## Scope

In scope:

- Claude Code (CLI) and Codex CLI on macOS.
- Session-level state per agent session: `agent_running`, `awaiting_user`, `inactive`.
- Concurrent sessions tracked independently via `(agent, session_id)` natural key.

Explicitly out of scope for v1:

- Native desktop apps (Claude desktop, Codex desktop) — agent state is not directly observable without screen/AX scraping; defer.
- Tool-call-level granularity (which tool, how long) — captured `agent_running` rolls everything up.
- Prompt or response content storage. The `raw` event column may incidentally contain hook payloads with prompt fragments; controlled by a config flag.

## Solution overview

Two capture paths feed one append-only event log per agent. A new `code_agent_activity` tracker reads both, materializes intervals, and writes to `~/personal_db/db.sqlite` alongside other trackers. Engagement insights come from query-time JOINs against the existing `mosspath_lite_events` table (already populated by the `mosspath_lite` tracker).

Capture mechanisms:

- **Claude Code** — install hooks (`SessionStart`, `UserPromptSubmit`, `Stop`, `SessionEnd`) with `async: true`. Each hook execs `personal-db code-agent-hook-write`, which appends one JSONL line to `~/personal_db/state/code_agent_hooks.jsonl`. The `PreToolUse`/`PostToolUse` hooks are *also* installed and logged, but ignored by v1 ingest classification — this is forward-compat scaffolding so a future v2 with tool-call granularity does not require the user to re-run the install dance.
- **Codex CLI** — Codex has no hook system, but its rollout JSONL (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`) records every `event_msg` with timestamps. The tracker parses these directly. No setup required.

The setup wizard installs Claude Code hooks via a one-click button that POSTs to a new generic daemon action endpoint; the daemon executes a tracker-provided handler that edits `~/.claude/settings.json` atomically.

## Architecture

```
┌─ Claude Code (CLI) ─┐                        ┌─ Codex CLI ─┐
│ hooks: SessionStart │                        │ writes      │
│   UserPromptSubmit  │                        │ rollout-    │
│   PreToolUse        │                        │ *.jsonl     │
│   PostToolUse       │                        │ to          │
│   Stop, SessionEnd  │                        │ ~/.codex/   │
│   (async: true)     │                        │ sessions/   │
└──────┬──────────────┘                        └──────┬──────┘
       │ exec `personal-db code-agent-hook-write`     │
       ▼                                              │
┌──────────────────────────────────────────┐          │
│ ~/personal_db/state/                     │          │
│   code_agent_hooks.jsonl  (append-only)  │          │
└──────────────────────────────────────────┘          │
       │                                              │
       └──────────────┬───────────────────────────────┘
                      ▼
        ┌─── code_agent_activity tracker ────┐
        │  ingest.py: parse both sources,    │
        │  emit state transitions, materialize│
        │  intervals, upsert to db.sqlite    │
        │  Runs on daemon schedule (5 min)   │
        └─────────────┬──────────────────────┘
                      ▼
              ~/personal_db/db.sqlite
              ├─ code_agent_events
              ├─ code_agent_intervals
              └─ (joins against mosspath_lite_events for engagement)
```

## Files

### New

- **`src/personal_db/templates/trackers/code_agent_activity/manifest.yaml`** — `permission_type: none`, `local_only: true`, `schedule.every: 5m`, `time_column: timestamp`, `granularity: event`. Setup steps: (1) install Claude Code hooks button, (2) verify hooks status badge, (3) explainer text noting Codex CLI requires no setup. Config flag `store_raw: bool` (default `true`).
- **`src/personal_db/templates/trackers/code_agent_activity/schema.sql`** — `code_agent_events` and `code_agent_intervals` tables (full DDL in Schema section below).
- **`src/personal_db/templates/trackers/code_agent_activity/ingest.py`** — `sync(t)` reads both sources via separate cursors, derives state-transition events, materializes intervals. `backfill(t, start, end)` walks all rollout files in date range and replays the hooks JSONL from offset 0, idempotently re-upserting.
- **`src/personal_db/templates/trackers/code_agent_activity/visualizations.py`** — four renderers: weekly heatmap of agent runtime, awaiting-user vs running stacked bars, prompt-cadence histogram, engagement scatter (joins `mosspath_lite_events`).
- **`src/personal_db/templates/trackers/code_agent_activity/actions.py`** — handlers `install_hooks(cfg) -> {ok, message}`, `uninstall_hooks(cfg) -> {ok, message}`, `verify_hooks(cfg) -> {installed: bool, ours_present: bool, message}`. Pure Python functions the daemon imports.
- **`src/personal_db/cli/code_agent_hook_cmd.py`** — typer subcommand `personal-db code-agent-hook-write`. Reads JSON from stdin, parses `hook_event_name`/`session_id`/`cwd` from the standard Claude Code hook payload, appends one line to `~/personal_db/state/code_agent_hooks.jsonl` opened with `O_APPEND`. Pure stdlib. Always exits 0; routes failures to stderr only.

### Modified

- **`src/personal_db/daemon/http.py`** — add `POST /api/trackers/{name}/actions/{action}` endpoint. Resolves the installed tracker dir, imports `actions.py` if present, calls the named handler, returns `{ok, message, ...}`. Generic — not `code_agent_activity`-specific.
- **`src/personal_db/ui/templates/setup_tracker.html`** — render new step kinds: `install_hooks` (button calling the action endpoint via fetch, with inline result display) and `verify_hooks` (status badge that runs once on page load).
- **`src/personal_db/ui/setup_runner.py`** — handle the new step kinds in the step dispatcher.
- **`src/personal_db/cli/main.py`** — register `code-agent-hook-write` typer subcommand.

## Schema

```sql
-- code_agent_events: raw state transitions, append-only semantics
CREATE TABLE code_agent_events (
  agent         TEXT NOT NULL,           -- 'claude_code' | 'codex_cli'
  session_id    TEXT NOT NULL,
  timestamp     TEXT NOT NULL,           -- ISO-8601 UTC, ms precision
  event_type    TEXT NOT NULL,           -- 'session_start' | 'prompt_submitted'
                                         -- | 'awaiting_user' | 'session_ended'
  cwd           TEXT,
  git_branch    TEXT,
  source_file   TEXT,                    -- for Codex: rollout file path
  raw           TEXT,                    -- original JSON line (forensics);
                                         -- nulled when manifest store_raw=false
  PRIMARY KEY (agent, session_id, timestamp, event_type)
);
CREATE INDEX idx_events_session ON code_agent_events(agent, session_id);
CREATE INDEX idx_events_ts ON code_agent_events(timestamp);

-- code_agent_intervals: materialized from events on each sync
CREATE TABLE code_agent_intervals (
  agent             TEXT NOT NULL,
  session_id        TEXT NOT NULL,
  start_ts          TEXT NOT NULL,
  end_ts            TEXT NOT NULL,
  state             TEXT NOT NULL,       -- 'agent_running' | 'awaiting_user' | 'inactive'
  duration_seconds  REAL NOT NULL,
  cwd               TEXT,
  git_branch        TEXT,
  PRIMARY KEY (agent, session_id, start_ts)
);
CREATE INDEX idx_intervals_state_ts ON code_agent_intervals(state, start_ts);
```

Cursor state in `~/personal_db/state/cursors.sqlite`:

- `code_agent_activity:claude_hooks_offset` — byte offset into `code_agent_hooks.jsonl`.
- `code_agent_activity:codex_files` — JSON map `{file_path: byte_offset}` per rollout file seen.

## Sync flow

`ingest.sync(t)` performs:

1. **Claude Code source.** Open `~/personal_db/state/code_agent_hooks.jsonl`, seek to `claude_hooks_offset`, parse new lines, classify into the four v1 `event_type` values (`session_start`, `prompt_submitted`, `awaiting_user`, `session_ended`), silently drop known-but-unused hook events (`PreToolUse`, `PostToolUse`), upsert into `code_agent_events`. Save updated offset.
2. **Codex source.** Glob rollout files modified since the most recent cursor timestamp. For each, seek to its per-file byte offset, parse new `event_msg` rows from the rollout JSONL, classify into the four v1 event types, upsert. Save updated per-file offsets.
3. **Materialize intervals.** For each `(agent, session_id)` whose events table got new rows: `DELETE FROM code_agent_intervals WHERE agent = ? AND session_id = ?`, walk events ordered by `timestamp`, emit one interval per gap between transitions, insert. Idempotent — partial sessions (no `session_ended` yet) materialize up to the last seen event; the next sync re-does them correctly.

State derivation rules:

- `prompt_submitted` opens an `agent_running` interval until the next transition.
- `awaiting_user` (Claude Code: `Stop` hook; Codex: last `event_msg` of an assistant turn per the v1 heuristic) opens an `awaiting_user` interval.
- `session_ended` (Claude Code: `SessionEnd`; Codex: synthesized after 30 min of file inactivity) closes the session and drops it from the active set.
- `inactive` intervals only appear *between* sessions, never within them. Gaps inside a session retain whatever the last known state was. We do not fabricate `inactive` intervals for system sleep.

Synthetic `session_ended` rule (covers terminal kill / crash): during materialization, if a session's last event is older than 60 minutes and has no `session_ended`, emit a synthetic one at `last_event_ts + 1s`. Mark in `raw` as `{"synthetic": true}`.

## Engagement query (no new storage)

```sql
-- For each `agent_running` interval, total keystrokes the user produced
-- during it (across any app — i.e., "was the user actively at the keyboard
-- somewhere while the agent ran"). To narrow to "engaged with the agent's
-- terminal specifically", add `AND m.bundle_id IN ('com.apple.Terminal',
-- 'com.googlecode.iterm2', ...)`.
SELECT i.agent, i.session_id, i.start_ts, i.end_ts,
       i.duration_seconds,
       SUM(m.key_count) AS keystrokes_during
FROM code_agent_intervals i
LEFT JOIN mosspath_lite_events m
  ON m.timestamp >= i.start_ts
 AND m.timestamp <  i.end_ts
 AND m.action_type = 'input_batch'
WHERE i.state = 'agent_running'
GROUP BY i.agent, i.session_id, i.start_ts;
```

Column names verified against the live `mosspath_lite_events` schema: `timestamp`, `action_type`, `bundle_id`, `key_count`, `mouse_count`, `scroll_count`, `app_name`, `window_title`. The `action_type = 'input_batch'` filter avoids double-counting from non-input rows. Two engagement metrics are useful — total keystrokes anywhere (proxy for "user awake and working") and keystrokes filtered by terminal bundle id (proxy for "user engaged with this agent specifically"). Both are derivable from the same JOIN.

## Setup wizard flow

The `code_agent_activity` tracker manifest declares two new step kinds:

- **`install_hooks`** — renders a button. On click, the page POSTs to `/api/trackers/code_agent_activity/actions/install_hooks` and shows the response inline. The handler reads `~/.claude/settings.json` (creates if missing), deep-merges our hooks block under the existing `hooks` key without clobbering user entries, tags every entry it inserts with `"_personal_db_managed": true` so uninstall can find them, and writes back atomically via temp-file + rename. If the file is malformed JSON, the handler refuses to write and returns an error message.
- **`verify_hooks`** — runs once on page load via `GET /api/trackers/code_agent_activity/actions/verify_hooks`. Returns `{installed, ours_present, message}`; the page renders a green/red badge.

Hook command path resolution: at `install_hooks` time, the handler resolves `personal-db` via `shutil.which("personal-db")`; if not on `PATH`, falls back to `f"{sys.executable} -m personal_db"`. The resolved string is embedded in the hook config at install time so PATH changes don't break the hook.

The new daemon endpoint `POST /api/trackers/{name}/actions/{action}` is generic and reusable — any future tracker can ship an `actions.py`. Architectural fit: the daemon already executes tracker-provided code on a schedule (sync); executing tracker-provided code on a button click is the same shape, just user-initiated. No new privilege or trust boundary.

Codex CLI path is informational-only in the wizard: no install action, just a one-line note that activity from `~/.codex/sessions/` will be picked up automatically on the next sync.

## Error handling

- **Hook writer must never break Claude Code.** Hooks are installed `async: true` so Claude Code does not block on them, but the writer is still defensive: `O_APPEND` open, write line, close, exit 0 on any internal exception. Stderr is captured by Claude Code's hook log for debugging.
- **Concurrent hook writes** from multiple Claude Code sessions are safe without locking — POSIX guarantees `O_APPEND` writes < `PIPE_BUF` (4 KB) are atomic; our lines are ~200 bytes.
- **Cursor desync** for the hooks file: if `claude_hooks_offset` exceeds current file size (rotation/truncation), reset to 0 and log a warning. The events PRIMARY KEY makes re-ingestion idempotent.
- **Cursor desync for Codex files**: if a tracked rollout file disappears, drop its entry silently. If a file shrinks (defensive), reset its offset to 0.
- **Malformed JSON** in either source: skip the line, increment a `skipped_lines` counter in the sync result, continue. Never crash sync on external bad data. Distinct from this: rows with a known-but-unused `hook_event_name` (`PreToolUse`/`PostToolUse`) are silently dropped at classification time, not counted as malformed.
- **Settings.json conflicts**: deep-merge tagged with `_personal_db_managed: true`. Atomic temp-file + rename. Refuse to write if existing JSON is malformed; surface error to UI.
- **Daemon not running** when the user clicks "Install hooks": surface "Daemon not running — run `personal-db daemon install`" inline.
- **Codex `awaiting_user` heuristic** is best-effort. The rollout-JSONL parser is a pure function `(jsonl_lines) → events[]`; the v1 heuristic is iterated against the test corpus rather than rewritten.
- **Privacy.** Manifest config flag `store_raw: bool` (default `true`). When `false`, sync drops the `raw` column on upsert. Easy to toggle later.

## Testing

Unit tests under `tests/unit/`:

- **`test_code_agent_hook_writer.py`** — drives the `personal-db code-agent-hook-write` CLI. Asserts well-formed line append, exit code always 0, concurrent writes from 10 workers × 100 lines all parse intact.
- **`test_code_agent_ingest_claude.py`** — drives `ingest.sync()` against synthetic `code_agent_hooks.jsonl` covering: clean session, mid-session resume, synthetic `session_ended` after 60 min, file rotation/truncation, malformed line in middle.
- **`test_code_agent_ingest_codex.py`** — fixture corpus of anonymized real rollout JSONLs (user content replaced with `"<redacted>"`, structure and timestamps preserved). Asserts state classification matches expected events and intervals. This corpus is what the heuristic is iterated against.
- **`test_code_agent_intervals.py`** — pure-function interval materialization. Property-based: `end_ts` of every interval equals `start_ts` of the next for the same session; sum of durations equals `last_event_ts − first_event_ts`; `agent_running` always followed by `awaiting_user` or `session_ended`.
- **`test_code_agent_actions.py`** — `install_hooks`, `uninstall_hooks`, `verify_hooks` against a tmp `settings.json`. Covers: missing file (creates), existing user hooks (preserved, only ours added), reinstall (idempotent), uninstall (only managed entries removed), malformed file (refuses, returns error).
- **`test_daemon_actions_endpoint.py`** — `POST /api/trackers/{name}/actions/{action}` against a fake tracker dir with stub `actions.py`. 404 for unknown tracker/action, 500 with captured message on handler exception.

Integration test under `tests/integration/`:

- **`test_connector_code_agent_activity.py`** — full stack: install the tracker into a tmp root, drop a fixture `code_agent_hooks.jsonl` and a fixture rollout file, trigger sync via the daemon HTTP endpoint, assert rows land in `db.sqlite` and intervals materialize correctly. Mirrors the existing `test_connector_mosspath_lite.py` pattern.

Not tested:

- Real Claude Code or real Codex CLI processes. The hook writer and parsers are pure functions on file I/O.
- The setup-wizard button click itself. The action endpoint is unit-tested; the JS is a thin fetch wrapper covered by visual smoke during the wizard.

## Out-of-scope considered alternatives

- **Extending `claude_conversations` / `codex_conversations`.** Those trackers map content; this maps runtime state. Conflating doubles their schema migration risk for marginal savings.
- **Capturing in mosspath-lite (Swift) and ingesting via the existing `mosspath_lite` tracker.** Would require a Swift port of trivially Python parsing logic, cross-repo coupling, and breaks for sessions where mosspath-lite isn't running.
- **Hooks-direct-to-DB.** Hook scripts would need sqlite3, error handling that doesn't block Claude Code, and DB-path discovery. Violates the established tracker pattern (file → ingest → upsert) for ~5-minute lag savings the use case doesn't need.
- **Tool-call-level granularity.** Captured for free in both sources but expands the schema and adds a privacy surface (tool args include file paths, commands). Defer to v2.
