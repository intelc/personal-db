# Fold conversation trackers into code_agent_activity — design

**Date:** 2026-05-10
**Status:** Approved, pending implementation plan
**Branch:** worktree-feat+code-agent-merge

## Problem

Three trackers cover overlapping ground for AI coding-agent activity:

1. **`claude_conversations`** — reads `~/.claude/projects/*/<uuid>.jsonl`, stores per-session rollup rows in `claude_sessions` (project_slug, started_at, last_msg_at, message counts, first_user_prompt). Cadence: 1h.
2. **`codex_conversations`** — reads `~/.codex/sessions/*/rollout.jsonl` plus `~/.codex/history.jsonl`, stores per-session rollup rows in `codex_sessions` (cwd, started_at, last_event_at, message counts, first_user_prompt). Cadence: 1h.
3. **`code_agent_activity`** — reads Claude Code hooks plus the *same* Codex rollout JSONL files, stores `code_agent_events` (state-transition events) and `code_agent_intervals` (computed `agent_running`/`awaiting_user`/`inactive` durations). Cadence: 5m.

Pain points:

- Codex rollout JSONL is parsed by two trackers on overlapping schedules.
- Cross-tracker queries ("what was the first user prompt during this 2-hour `awaiting_user` gap?") require JOINs across schemas owned by different trackers.
- Three trackers must be installed, scheduled, and documented for one conceptual domain.
- Adding a third agent (Cursor, Aider) requires either splitting it across multiple trackers or deciding which existing one to extend.

## Goal

Make `code_agent_activity` the single tracker for all coding-agent observability — runtime state *and* per-session content rollup — by adding a third table (`code_agent_sessions`) and removing the two conversation trackers. Once merged, an interval can be enriched with the project, prompt, and message counts of its session via a single JOIN on `(agent, session_id)`.

## Scope

In scope:

- New `code_agent_sessions` table on the `code_agent_activity` tracker.
- Ingest changes to populate it from JSONL during the same parse pass that produces events/intervals.
- One-shot in-place migration that backfills from `claude_sessions`/`codex_sessions`, drops them, and removes the now-stale installed tracker dirs at `<root>/trackers/{claude,codex}_conversations/`.
- Deletion of the two bundled tracker templates from `src/personal_db/templates/trackers/`.
- Enrichment of existing `code_agent_activity` visualizations with session content; deletion of the standalone conversation viz modules.

Explicitly out of scope:

- Renaming `code_agent_activity` (the name still fits with content rollups added).
- New visualizations beyond enrichment of existing charts.
- Adding Cursor / Aider / other agents.
- Changing the `code_agent_events` or `code_agent_intervals` schemas.
- Backwards-compatibility views for `claude_sessions`/`codex_sessions`.

## Solution overview

`code_agent_activity` grows from two tables to three. The new `code_agent_sessions` table is keyed `(agent, session_id)` — the same natural key used elsewhere in the tracker. The ingest pipeline gains a "session rollup" producer that runs after event ingestion, walks the JSONL files for each agent, and upserts one row per session.

A migration step runs at the top of every ingest. It is naturally idempotent: it only acts on legacy artifacts (`claude_sessions`/`codex_sessions` tables, stale installed tracker directories) and those artifacts are gone after the first run. On every ingest it:

1. Backfills `code_agent_sessions` from `claude_sessions` and `codex_sessions` if those tables exist, then drops them.
2. Removes the legacy installed tracker directories from `<root>/trackers/` if present.

After the first successful run, all four checks become no-ops costing one `PRAGMA`/`os.path.exists` each — cheap enough to leave unconditional.

The two bundled templates are deleted from the package. Existing users keep working without manual intervention because the migration step removes the orphaned installed copies on the next sync of `code_agent_activity`.

## Schema additions

Appended to `src/personal_db/templates/trackers/code_agent_activity/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS code_agent_sessions (
  agent               TEXT NOT NULL,        -- 'claude_code' | 'codex'
  session_id          TEXT NOT NULL,        -- agent-assigned UUID
  cwd                 TEXT,                 -- absolute path; resolution rules below
  started_at          TEXT NOT NULL,        -- ISO-8601 UTC, earliest user/assistant message
  last_msg_at         TEXT NOT NULL,        -- ISO-8601 UTC, latest user/assistant message
  message_count       INTEGER NOT NULL,
  user_msg_count      INTEGER NOT NULL,
  assistant_msg_count INTEGER NOT NULL,
  first_user_prompt   TEXT,                 -- first user message text, truncated to 500 chars
  source_file         TEXT,                 -- absolute path of originating JSONL
  PRIMARY KEY (agent, session_id)
);

CREATE INDEX IF NOT EXISTS idx_code_agent_sessions_started ON code_agent_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_code_agent_sessions_cwd ON code_agent_sessions(cwd);
```

The corresponding block is appended to `manifest.yaml`'s `schema.tables`.

`code_agent_events` and `code_agent_intervals` are unchanged.

## Ingest changes

The existing `code_agent_activity/ingest.py` keeps its event and interval producers. After they finish, a new session-rollup phase runs:

```
for each agent in (claude_code, codex):
    for each JSONL file newer than the last cursor:
        parse_session_rollup(file)
        upsert into code_agent_sessions
    advance cursor
```

Two helpers are added:

- **`_parse_claude_session(jsonl_path) -> dict | None`**
  Logic ported from `claude_conversations/ingest.py`, with one extension: each user/assistant message line in Claude's JSONL carries a `cwd` field at the top level; the parser captures the `cwd` from the *latest* message and returns it alongside the rollup. Skip-types (`permission-mode`, `attachment`, `file-history-snapshot`, `system`, `last-prompt`, `queue-operation`) are unchanged.

- **`_parse_codex_session(rollout_path, history_map) -> dict | None`**
  Logic ported from `codex_conversations/ingest.py`. `history_map` is the `session_id → first_prompt` dict already loaded once per ingest from `~/.codex/history.jsonl`. `cwd` comes from the `session_meta.turn_context.cwd` field, exactly as today.

### `first_user_prompt` resolution (Claude)

1. **Primary:** the first user message text from the JSONL (truncated 500 chars), as `claude_conversations` does today.
2. **Fallback:** if a session_id appears in `code_agent_events` with `event_type='user_prompt_submit'` but no JSONL file exists for it, take the prompt text from the earliest such event's `raw` payload.

### `cwd` resolution

| Agent | Priority |
|---|---|
| Claude | (a) `cwd` from latest JSONL message line → (b) `cwd` from earliest hook event for that session → (c) reverse-mapped project_slug (best-effort, ambiguous; document the limitation) |
| Codex | `session_meta.turn_context.cwd` from rollout JSONL (existing behavior). |

The reverse-mapped slug fallback for Claude is a last resort; in practice (a) covers virtually all sessions, and (b) covers any session captured by hooks since install.

### Cursors

JSONL discovery uses mtime-based incremental scanning so the 5-minute cadence is cheap on large session corpora. The session-rollup phase keeps its own cursor (separate from the events/intervals cursor) so a partial failure in one phase doesn't reset the other.

## Migration

A new function `_run_legacy_migration(conn, root)` is called at the top of `ingest.run()` (after `_ensure_schema_columns`):

```python
def _run_legacy_migration(conn, root):
    # Backfill claude_sessions if present, then drop.
    if _table_exists(conn, "claude_sessions"):
        conn.execute("""
            INSERT OR IGNORE INTO code_agent_sessions
              (agent, session_id, cwd, started_at, last_msg_at,
               message_count, user_msg_count, assistant_msg_count,
               first_user_prompt, source_file)
            SELECT 'claude_code', session_id, NULL, started_at, last_msg_at,
                   message_count, user_msg_count, assistant_msg_count,
                   first_user_prompt, NULL
            FROM claude_sessions
        """)
        conn.execute("DROP TABLE claude_sessions")

    # Backfill codex_sessions if present (cwd carried over), then drop.
    if _table_exists(conn, "codex_sessions"):
        conn.execute("""
            INSERT OR IGNORE INTO code_agent_sessions
              (agent, session_id, cwd, started_at, last_msg_at,
               message_count, user_msg_count, assistant_msg_count,
               first_user_prompt, source_file)
            SELECT 'codex', session_id, cwd, started_at, last_event_at,
                   event_count, user_msg_count, assistant_msg_count,
                   first_user_prompt, NULL
            FROM codex_sessions
        """)
        conn.execute("DROP TABLE codex_sessions")

    conn.commit()

    # Remove orphaned installed tracker dirs (only if the contents look like
    # the canonical four-file template; otherwise warn and leave alone).
    for stale in ("claude_conversations", "codex_conversations"):
        d = root / "trackers" / stale
        if d.exists() and _is_canonical_tracker_dir(d):
            shutil.rmtree(d)
        elif d.exists():
            log.warning(
                "code_agent_activity: leaving %s in place (non-canonical contents)", d
            )
```

`_is_canonical_tracker_dir(d)` is a one-liner sniff that returns True iff `d` contains `manifest.yaml`, `ingest.py`, `schema.sql`, `visualizations.py` and nothing else (excluding `__pycache__`, cursor-style state). This protects user customizations.

Properties:

- **Idempotent by construction.** All branches gate on legacy-artifact existence; once removed, every branch is skipped on subsequent runs.
- **Safe under concurrent runs.** Wrapped in a single sqlite transaction; sqlite serializes writes.
- **Lossless for content fields** (counts, prompts) but `cwd` is left NULL for migrated Claude rows — the next session-rollup pass repopulates it from the JSONL files that still exist on disk.
- **Stale rows are tolerated.** If a JSONL file is gone, the migrated row remains queryable; it just won't get its `cwd` populated.

## Template deletions

Removed entirely from the package:

- `src/personal_db/templates/trackers/claude_conversations/` (manifest.yaml, ingest.py, schema.sql, visualizations.py)
- `src/personal_db/templates/trackers/codex_conversations/` (same four files)

Modified:

- `src/personal_db/templates/trackers/code_agent_activity/manifest.yaml` — adds `code_agent_sessions` table block.
- `src/personal_db/templates/trackers/code_agent_activity/schema.sql` — appends `CREATE TABLE` and indexes.
- `src/personal_db/templates/trackers/code_agent_activity/ingest.py` — adds session-rollup producer, migration step, and helpers.
- `src/personal_db/templates/trackers/code_agent_activity/visualizations.py` — enrichment passes (see below).

After applying these changes, `personal_db.installer.list_bundled()` no longer returns the two old templates.

## Visualization changes

All work stays inside `code_agent_activity/visualizations.py` (currently 655 LOC). Two enrichment passes:

1. **Per-session timeline** (existing 24h chart) gains a header row per session showing `first_user_prompt` (truncated) and `cwd`, fetched via JOIN to `code_agent_sessions`.
2. **Charts that group/label by `cwd`** switch from reading `code_agent_intervals.cwd` (start-time only, occasionally NULL) to a JOIN through `code_agent_sessions` for a more reliable label.

The standalone viz files (`claude_conversations/visualizations.py`, `codex_conversations/visualizations.py`) are deleted with their templates.

## Tests

New unit tests under `tests/unit/`:

- **`test_code_agent_sessions.py`**
  - Claude JSONL fixture → correct rollup row, `cwd` taken from message metadata.
  - Codex rollout fixture + history.jsonl → correct rollup row, `cwd` from `session_meta`.
  - Claude session present in `code_agent_events` only (no JSONL) → `first_user_prompt` populated from earliest `user_prompt_submit` event.
  - Re-running ingest on the same fixture → `INSERT OR REPLACE` upsert; row count unchanged.

- **`test_code_agent_migration.py`**
  - DB pre-populated with `claude_sessions` + `codex_sessions` rows → after ingest, those rows present in `code_agent_sessions`, old tables dropped.
  - Second ingest on the same DB → migration short-circuits cleanly (legacy tables already gone; no errors).
  - `<root>/trackers/claude_conversations/` with canonical contents → removed after ingest.
  - `<root>/trackers/claude_conversations/` with an extra user file → left in place, warning logged.
  - Non-conversation trackers under `<root>/trackers/` untouched.

Existing tests:

- `tests/unit/test_claude_conversations.py` and `tests/unit/test_codex_conversations.py` — retargeted: the parsing logic they exercise has moved into `code_agent_activity/ingest.py`, so the tests are renamed and re-pointed at the new module. Fixture files reused as-is.
- `tests/unit/test_smoke.py` and `tests/unit/test_installer.py` — verify the two old templates no longer appear in `list_bundled()`.

## Documentation

- `CLAUDE.md` — no change needed; the existing "Editing a bundled tracker — required SOP" section already covers `tracker reinstall`.
- Spec README/changelog (if present) — note the merge.

## Rollout sequence (for the implementation plan)

1. Schema + manifest changes (add `code_agent_sessions` table and indexes).
2. Migration helper, with tests.
3. Ingest helpers (`_parse_claude_session`, `_parse_codex_session`), with tests.
4. Wire session-rollup phase into `ingest.run()`, with end-to-end test.
5. Visualization enrichment.
6. Delete bundled templates and standalone viz files.
7. Retarget existing conversation tests.
8. Run `personal-db --root ~/personal_db tracker reinstall code_agent_activity` against a real install to validate migration.

## Risks and mitigations

- **Risk:** A user has manually customized their installed `<root>/trackers/claude_conversations/` and the migration deletes it. **Mitigation:** the migration only removes the directory if its contents match the canonical four-file template (`manifest.yaml`, `ingest.py`, `schema.sql`, `visualizations.py`, plus permitted noise like `__pycache__`); otherwise it logs a warning and leaves the directory in place for the user to remove manually.
- **Risk:** Migration backfills a row that conflicts with a row written by the same ingest run's session-rollup phase. **Mitigation:** migration uses `INSERT OR IGNORE`, then session-rollup uses `INSERT OR REPLACE`, so live data wins over stale backfilled data.
- **Risk:** Reverse-mapping `project_slug → cwd` for Claude is ambiguous (`-` is the joiner for both `/` and `_`). **Mitigation:** never reverse-map for migrated rows; rely on the JSONL re-parse to fill `cwd` correctly. The reverse-map fallback is only used at parse time when the JSONL has no message-level `cwd`, which in practice is extremely rare.

## Open questions

None at design time. Implementation may surface edge cases in the JSONL parsers that are best fixed against fixtures.
