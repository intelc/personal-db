# Code-agent merge implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold `claude_conversations` and `codex_conversations` into the `code_agent_activity` tracker by adding a third table (`code_agent_sessions`), with a one-shot in-place migration that backfills legacy data and removes stale installed copies.

**Architecture:** `code_agent_activity` grows a sibling module `sessions.py` that parses Claude JSONL (`~/.claude/projects/*/<sid>.jsonl`) and Codex rollout JSONL (`~/.codex/sessions/.../rollout-*.jsonl`) into per-session rollups. The existing `ingest.run()` runs three phases per sync: schema column upgrade (existing) → legacy migration (new) → events/intervals (existing) → session rollup (new). Migration is idempotent by construction — every branch gates on the existence of a legacy artifact.

**Tech Stack:** Python 3.11, sqlite3, pytest, the `personal_db.tracker.Tracker` cursor abstraction, and the `_load_sibling` importlib pattern already used in `code_agent_activity/ingest.py`.

**Spec:** `docs/superpowers/specs/2026-05-10-code-agent-merge-design.md`

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `src/personal_db/templates/trackers/code_agent_activity/sessions.py` | Create | Pure parsers `_parse_claude_session(jsonl_path)` and `_parse_codex_session(rollout_path, history_map)`; helpers `_load_codex_history_first_prompts()`. Loaded as a sibling of `parsers.py`/`intervals.py`. |
| `src/personal_db/templates/trackers/code_agent_activity/schema.sql` | Modify | Append `CREATE TABLE code_agent_sessions` + indexes. |
| `src/personal_db/templates/trackers/code_agent_activity/manifest.yaml` | Modify | Append `code_agent_sessions` block under `schema.tables`. |
| `src/personal_db/templates/trackers/code_agent_activity/ingest.py` | Modify | Load `sessions` sibling; add `_table_exists`, `_is_canonical_tracker_dir`, `_run_legacy_migration`; add session-rollup phase to `sync()`; thread session cursor through `state`. |
| `src/personal_db/templates/trackers/code_agent_activity/visualizations.py` | Modify | JOIN `code_agent_sessions` for cwd labels and per-session timeline header (first_user_prompt). |
| `src/personal_db/templates/trackers/claude_conversations/` | Delete | Entire directory — no longer bundled. |
| `src/personal_db/templates/trackers/codex_conversations/` | Delete | Entire directory — no longer bundled. |
| `tests/unit/test_code_agent_sessions.py` | Create | Unit tests for the new parsers, the rollup upsert path, the hook-event fallback for `first_user_prompt`. |
| `tests/unit/test_code_agent_migration.py` | Create | Unit tests for `_table_exists`, `_is_canonical_tracker_dir`, `_run_legacy_migration`. |
| `tests/unit/test_installer.py` | Modify | Drop `claude_conversations` and `codex_conversations` from the bundled-set assertion. |
| `tests/integration/test_connector_claude_conversations.py` | Delete | Tracker no longer exists. |
| `tests/integration/test_connector_codex_conversations.py` | Delete | Tracker no longer exists. |
| `tests/fixtures/claude_conversations/` | Move | → `tests/fixtures/code_agent_activity/claude_projects/` (path used by new tests). |
| `tests/fixtures/codex_conversations/` | Move | → `tests/fixtures/code_agent_activity/codex_sessions/` (path used by new tests). |

---

## Task 1: Update Claude fixture to carry message-level `cwd`

Real Claude JSONL records `cwd`, `gitBranch`, `version` on every user/assistant line. The current fixture is missing these. Update it before writing parser tests so the tests reflect production shape.

**Files:**
- Modify: `tests/fixtures/claude_conversations/projects/-test-project/abc123.jsonl`

- [ ] **Step 1: Read existing fixture**

```bash
cat tests/fixtures/claude_conversations/projects/-test-project/abc123.jsonl
```

Expected: 4 lines (system, user, assistant, user).

- [ ] **Step 2: Rewrite fixture with cwd on user/assistant lines**

Overwrite `tests/fixtures/claude_conversations/projects/-test-project/abc123.jsonl` with:

```jsonl
{"type":"system","timestamp":"2026-04-26T10:00:00.000Z"}
{"type":"user","timestamp":"2026-04-26T10:00:01.000Z","sessionId":"abc123","cwd":"/Users/test/code/example","gitBranch":"main","message":{"content":"hello, can you help me debug?"}}
{"type":"assistant","timestamp":"2026-04-26T10:00:02.000Z","sessionId":"abc123","cwd":"/Users/test/code/example","gitBranch":"main","message":{"content":[{"type":"text","text":"Sure, what's the error?"}]}}
{"type":"user","timestamp":"2026-04-26T10:00:30.000Z","sessionId":"abc123","cwd":"/Users/test/code/example","gitBranch":"main","message":{"content":[{"type":"text","text":"NullPointerException"}]}}
```

- [ ] **Step 3: Verify existing claude_conversations integration test still passes**

Run: `.venv/bin/python -m pytest tests/integration/test_connector_claude_conversations.py -v`
Expected: PASS (it doesn't read `cwd`, so it's unaffected).

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/claude_conversations/projects/-test-project/abc123.jsonl
git commit -m "test(fixtures): add cwd/gitBranch to claude session fixture"
```

---

## Task 2: Move conversation fixtures under code_agent_activity

Move fixtures so the new test files reference them under a stable path that won't be touched when the old trackers are deleted later.

**Files:**
- Move: `tests/fixtures/claude_conversations/` → `tests/fixtures/code_agent_activity/claude_projects/`
- Move: `tests/fixtures/codex_conversations/` → `tests/fixtures/code_agent_activity/codex_sessions/`

- [ ] **Step 1: Create destination dir**

```bash
mkdir -p tests/fixtures/code_agent_activity
```

- [ ] **Step 2: Move with git**

```bash
git mv tests/fixtures/claude_conversations tests/fixtures/code_agent_activity/claude_projects
git mv tests/fixtures/codex_conversations tests/fixtures/code_agent_activity/codex_sessions
```

- [ ] **Step 3: Update existing integration tests that point at old paths**

Modify `tests/integration/test_connector_claude_conversations.py:11`:

```python
FIXTURE_PROJECTS = Path("tests/fixtures/code_agent_activity/claude_projects/projects")
```

Modify `tests/integration/test_connector_codex_conversations.py` similarly. Search for `tests/fixtures/codex_conversations` and replace with `tests/fixtures/code_agent_activity/codex_sessions`.

- [ ] **Step 4: Run the existing integration tests**

```bash
.venv/bin/python -m pytest tests/integration/test_connector_claude_conversations.py tests/integration/test_connector_codex_conversations.py -v
```

Expected: PASS for both. (We delete these tests in a later task; this just confirms the fixture move is clean.)

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures tests/integration
git commit -m "test(fixtures): move conversation fixtures under code_agent_activity"
```

---

## Task 3: Add code_agent_sessions table to schema.sql and manifest.yaml

Land the new table on the canonical template files. Existing installs pick this up via `personal-db tracker reinstall code_agent_activity`, which re-applies `schema.sql`.

**Files:**
- Modify: `src/personal_db/templates/trackers/code_agent_activity/schema.sql`
- Modify: `src/personal_db/templates/trackers/code_agent_activity/manifest.yaml`

- [ ] **Step 1: Append to schema.sql**

Append to `src/personal_db/templates/trackers/code_agent_activity/schema.sql`:

```sql

CREATE TABLE IF NOT EXISTS code_agent_sessions (
  agent               TEXT NOT NULL,
  session_id          TEXT NOT NULL,
  cwd                 TEXT,
  started_at          TEXT NOT NULL,
  last_msg_at         TEXT NOT NULL,
  message_count       INTEGER NOT NULL,
  user_msg_count      INTEGER NOT NULL,
  assistant_msg_count INTEGER NOT NULL,
  first_user_prompt   TEXT,
  source_file         TEXT,
  PRIMARY KEY (agent, session_id)
);

CREATE INDEX IF NOT EXISTS idx_code_agent_sessions_started ON code_agent_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_code_agent_sessions_cwd ON code_agent_sessions(cwd);
```

- [ ] **Step 2: Append to manifest.yaml**

Append under `schema.tables:` in `src/personal_db/templates/trackers/code_agent_activity/manifest.yaml`:

```yaml
    code_agent_sessions:
      columns:
        agent:               {type: TEXT,    semantic: "agent name, e.g. claude_code or codex"}
        session_id:          {type: TEXT,    semantic: "agent-assigned session identifier"}
        cwd:                 {type: TEXT,    semantic: "absolute working directory; resolved from JSONL message metadata or hook events"}
        started_at:          {type: TEXT,    semantic: "ISO-8601 UTC, earliest user/assistant message timestamp"}
        last_msg_at:         {type: TEXT,    semantic: "ISO-8601 UTC, latest user/assistant message timestamp"}
        message_count:       {type: INTEGER, semantic: "user + assistant messages"}
        user_msg_count:      {type: INTEGER, semantic: "user messages"}
        assistant_msg_count: {type: INTEGER, semantic: "assistant messages"}
        first_user_prompt:   {type: TEXT,    semantic: "first user message text, truncated to 500 chars"}
        source_file:         {type: TEXT,    semantic: "absolute path of originating JSONL"}
```

- [ ] **Step 3: Validate manifest parses**

```bash
.venv/bin/python -c "from pathlib import Path; from personal_db.manifest import load_manifest; m = load_manifest(Path('src/personal_db/templates/trackers/code_agent_activity/manifest.yaml')); print(sorted(m.schema.tables))"
```

Expected output: `['code_agent_events', 'code_agent_intervals', 'code_agent_sessions']`

- [ ] **Step 4: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/schema.sql src/personal_db/templates/trackers/code_agent_activity/manifest.yaml
git commit -m "feat(code-agent): add code_agent_sessions table"
```

---

## Task 4: Create sessions.py sibling — Claude parser (TDD)

`code_agent_activity/sessions.py` holds pure functions. The ingest module loads it via the existing `_load_sibling` pattern. Logic is ported from `claude_conversations/ingest.py` with one extension: extract `cwd` from the latest user/assistant message line.

**Files:**
- Create: `src/personal_db/templates/trackers/code_agent_activity/sessions.py`
- Create: `tests/unit/test_code_agent_sessions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_code_agent_sessions.py`:

```python
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_PY = REPO_ROOT / "src/personal_db/templates/trackers/code_agent_activity/sessions.py"
FIXTURES = REPO_ROOT / "tests/fixtures/code_agent_activity"


def _load_sessions_module():
    spec = importlib.util.spec_from_file_location("_pdb_code_agent_sessions", SESSIONS_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_claude_session_extracts_cwd_from_message_metadata():
    mod = _load_sessions_module()
    jsonl = FIXTURES / "claude_projects/projects/-test-project/abc123.jsonl"
    row = mod.parse_claude_session(jsonl)
    assert row is not None
    assert row["agent"] == "claude_code"
    assert row["session_id"] == "abc123"
    assert row["cwd"] == "/Users/test/code/example"
    assert row["started_at"] == "2026-04-26T10:00:01.000Z"
    assert row["last_msg_at"] == "2026-04-26T10:00:30.000Z"
    assert row["message_count"] == 3
    assert row["user_msg_count"] == 2
    assert row["assistant_msg_count"] == 1
    assert row["first_user_prompt"] == "hello, can you help me debug?"
    assert row["source_file"].endswith("abc123.jsonl")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_sessions.py -v
```

Expected: FAIL — `sessions.py` does not exist.

- [ ] **Step 3: Create sessions.py with parse_claude_session**

Create `src/personal_db/templates/trackers/code_agent_activity/sessions.py`:

```python
"""Per-session rollup parsers for code_agent_activity.

Loaded as a sibling of parsers.py/intervals.py via the importlib pattern in
ingest.py — see _load_sibling there.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_CLAUDE_SKIP_TYPES = {
    "permission-mode",
    "attachment",
    "file-history-snapshot",
    "system",
    "last-prompt",
    "queue-operation",
}


def claude_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECTS_DIR") or "~/.claude/projects").expanduser()


def codex_history_path() -> Path:
    return Path(os.environ.get("CODEX_HISTORY_FILE") or "~/.codex/history.jsonl").expanduser()


def _claude_extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def parse_claude_session(jsonl_path: Path) -> dict | None:
    """Parse a Claude Code session JSONL into a code_agent_sessions row.

    cwd is taken from the most recent user/assistant line that carries it.
    Returns None if the file has no user/assistant messages.
    """
    started_at = None
    last_msg_at = None
    message_count = 0
    user_msg_count = 0
    assistant_msg_count = 0
    first_user_prompt = None
    cwd = None
    session_id = jsonl_path.stem

    try:
        with jsonl_path.open(encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    line = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                msg_type = line.get("type", "")
                if msg_type in _CLAUDE_SKIP_TYPES:
                    continue
                if msg_type not in {"user", "assistant"}:
                    continue

                ts = line.get("timestamp")
                if ts:
                    if started_at is None or ts < started_at:
                        started_at = ts
                    if last_msg_at is None or ts > last_msg_at:
                        last_msg_at = ts

                line_cwd = line.get("cwd")
                if line_cwd:
                    cwd = line_cwd  # latest wins

                message_count += 1
                if msg_type == "user":
                    user_msg_count += 1
                    if first_user_prompt is None:
                        text = _claude_extract_text(line.get("message", {}).get("content", ""))
                        first_user_prompt = text[:500] if text else None
                else:
                    assistant_msg_count += 1
    except OSError as exc:
        log.warning("code_agent_activity: cannot read %s: %s", jsonl_path, exc)
        return None

    if started_at is None:
        return None

    return {
        "agent": "claude_code",
        "session_id": session_id,
        "cwd": cwd,
        "started_at": started_at,
        "last_msg_at": last_msg_at or started_at,
        "message_count": message_count,
        "user_msg_count": user_msg_count,
        "assistant_msg_count": assistant_msg_count,
        "first_user_prompt": first_user_prompt,
        "source_file": str(jsonl_path),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_sessions.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/sessions.py tests/unit/test_code_agent_sessions.py
git commit -m "feat(code-agent): claude session rollup parser"
```

---

## Task 5: Add Codex parser to sessions.py (TDD)

Port `_parse_session` from `codex_conversations/ingest.py` and the `history.jsonl` first-prompt loader.

**Files:**
- Modify: `src/personal_db/templates/trackers/code_agent_activity/sessions.py`
- Modify: `tests/unit/test_code_agent_sessions.py`

- [ ] **Step 1: Append failing test**

Append to `tests/unit/test_code_agent_sessions.py`:

```python
def test_parse_codex_session_extracts_cwd_and_first_prompt():
    mod = _load_sessions_module()
    jsonl = FIXTURES / "codex_sessions/sessions/2026/04/26/rollout-2026-04-26T10-00-00-550e8400-e29b-41d4-a716-446655440000.jsonl"
    row = mod.parse_codex_session(jsonl, history_map={})
    assert row is not None
    assert row["agent"] == "codex"
    assert row["session_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert row["cwd"] == "/Users/test/code/example"
    assert row["started_at"] == "2026-04-26T10:00:00.000Z"
    assert row["user_msg_count"] == 1
    assert row["assistant_msg_count"] == 1
    assert row["first_user_prompt"] == "Write a hello world in Python"


def test_parse_codex_session_history_overrides_first_prompt():
    mod = _load_sessions_module()
    jsonl = FIXTURES / "codex_sessions/sessions/2026/04/26/rollout-2026-04-26T10-00-00-550e8400-e29b-41d4-a716-446655440000.jsonl"
    row = mod.parse_codex_session(
        jsonl,
        history_map={"550e8400-e29b-41d4-a716-446655440000": "from-history"},
    )
    assert row["first_user_prompt"] == "from-history"


def test_load_codex_history_first_prompts_keeps_first(tmp_path):
    mod = _load_sessions_module()
    history = tmp_path / "history.jsonl"
    history.write_text(
        '{"session_id":"s1","ts":1,"text":"first"}\n'
        '{"session_id":"s1","ts":2,"text":"second"}\n'
        '{"session_id":"s2","ts":3,"text":"only"}\n'
    )
    out = mod.load_codex_history_first_prompts(history)
    assert out == {"s1": "first", "s2": "only"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_sessions.py -v
```

Expected: 3 fails (`parse_codex_session` and `load_codex_history_first_prompts` undefined).

- [ ] **Step 3: Append to sessions.py**

Append to `src/personal_db/templates/trackers/code_agent_activity/sessions.py`:

```python


def _codex_parse_payload(raw):
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        result = ast.literal_eval(raw)
        if isinstance(result, dict):
            return result
    except (ValueError, SyntaxError):
        pass
    return None


def _codex_extract_text(content) -> str:
    if isinstance(content, str):
        s = content.strip()
        if s.startswith("["):
            try:
                content = json.loads(s)
            except json.JSONDecodeError:
                try:
                    content = ast.literal_eval(s)
                except (ValueError, SyntaxError):
                    return s[:500]
        else:
            return s[:500]
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("input_text")
                if t:
                    parts.append(t)
        return " ".join(parts)[:500]
    return str(content)[:500]


def _codex_is_synthetic_user_message(text: str) -> bool:
    return text.startswith("# AGENTS.md instructions for ")


def _codex_filename_uuid(path: Path) -> str | None:
    parts = path.stem.split("-")
    for start in range(len(parts) - 1, -1, -1):
        candidate = "-".join(parts[start:])
        if len(candidate) == 36 and candidate.count("-") == 4:
            return candidate
    return path.stem


def load_codex_history_first_prompts(path: Path | None = None) -> dict[str, str]:
    """Map session_id → first user prompt text from ~/.codex/history.jsonl."""
    if path is None:
        path = codex_history_path()
    out: dict[str, str] = {}
    if not path.exists():
        return out
    with path.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = d.get("session_id")
            text = d.get("text")
            if sid and text and sid not in out:
                out[sid] = text[:500]
    return out


def parse_codex_session(jsonl_path: Path, history_map: dict[str, str]) -> dict | None:
    """Parse a Codex CLI rollout JSONL into a code_agent_sessions row."""
    session_id = None
    started_at = None
    last_event_at = None
    cwd = None
    message_count = 0
    user_msg_count = 0
    assistant_msg_count = 0
    first_user_prompt = None

    fallback_uuid = _codex_filename_uuid(jsonl_path)

    try:
        with jsonl_path.open(encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    line = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                line_type = line.get("type", "")
                ts = line.get("timestamp")
                if ts and (last_event_at is None or ts > last_event_at):
                    last_event_at = ts

                if line_type == "session_meta":
                    payload = _codex_parse_payload(line.get("payload"))
                    if payload is not None:
                        session_id = payload.get("id") or fallback_uuid
                        started_at = payload.get("timestamp")
                elif line_type == "turn_context":
                    if cwd is None:
                        payload = _codex_parse_payload(line.get("payload"))
                        if payload is not None:
                            cwd = payload.get("cwd")
                elif line_type == "response_item":
                    payload = _codex_parse_payload(line.get("payload"))
                    if payload is None:
                        continue
                    role = payload.get("role", "")
                    if role == "user":
                        text = _codex_extract_text(payload.get("content", ""))
                        if not _codex_is_synthetic_user_message(text):
                            message_count += 1
                            user_msg_count += 1
                            if first_user_prompt is None:
                                first_user_prompt = text[:500] if text else None
                    elif role == "assistant":
                        message_count += 1
                        assistant_msg_count += 1
    except OSError as exc:
        log.warning("code_agent_activity: cannot read %s: %s", jsonl_path, exc)
        return None

    if started_at is None:
        return None

    sid = session_id or fallback_uuid
    if sid in history_map:
        first_user_prompt = history_map[sid]

    return {
        "agent": "codex",
        "session_id": sid,
        "cwd": cwd,
        "started_at": started_at,
        "last_msg_at": last_event_at or started_at,
        "message_count": message_count,
        "user_msg_count": user_msg_count,
        "assistant_msg_count": assistant_msg_count,
        "first_user_prompt": first_user_prompt,
        "source_file": str(jsonl_path),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_sessions.py -v
```

Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/sessions.py tests/unit/test_code_agent_sessions.py
git commit -m "feat(code-agent): codex session rollup parser"
```

---

## Task 6: Migration helpers (TDD)

Add `_table_exists`, `_is_canonical_tracker_dir`, and `_run_legacy_migration` to `ingest.py`. The function only acts on legacy artifacts and is idempotent by construction.

**Files:**
- Modify: `src/personal_db/templates/trackers/code_agent_activity/ingest.py`
- Create: `tests/unit/test_code_agent_migration.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_code_agent_migration.py`:

```python
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INGEST_PY = REPO_ROOT / "src/personal_db/templates/trackers/code_agent_activity/ingest.py"


def _load_ingest_module():
    spec = importlib.util.spec_from_file_location("_pdb_code_agent_ingest", INGEST_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def db_with_legacy_tables(tmp_path: Path) -> Path:
    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE claude_sessions (
          session_id TEXT PRIMARY KEY,
          project_slug TEXT NOT NULL,
          started_at TEXT NOT NULL,
          last_msg_at TEXT NOT NULL,
          message_count INTEGER NOT NULL,
          user_msg_count INTEGER NOT NULL,
          assistant_msg_count INTEGER NOT NULL,
          first_user_prompt TEXT
        );
        CREATE TABLE codex_sessions (
          session_id TEXT PRIMARY KEY,
          cwd TEXT,
          started_at TEXT NOT NULL,
          last_event_at TEXT NOT NULL,
          event_count INTEGER NOT NULL,
          user_msg_count INTEGER NOT NULL,
          assistant_msg_count INTEGER NOT NULL,
          first_user_prompt TEXT
        );
        CREATE TABLE code_agent_sessions (
          agent TEXT NOT NULL,
          session_id TEXT NOT NULL,
          cwd TEXT,
          started_at TEXT NOT NULL,
          last_msg_at TEXT NOT NULL,
          message_count INTEGER NOT NULL,
          user_msg_count INTEGER NOT NULL,
          assistant_msg_count INTEGER NOT NULL,
          first_user_prompt TEXT,
          source_file TEXT,
          PRIMARY KEY (agent, session_id)
        );
        INSERT INTO claude_sessions VALUES ('c1','-x',  '2026-01-01T00:00:00Z','2026-01-01T01:00:00Z',5,3,2,'hi');
        INSERT INTO codex_sessions  VALUES ('x1','/repo','2026-01-02T00:00:00Z','2026-01-02T01:00:00Z',4,2,2,'hello');
    """)
    con.commit()
    con.close()
    return db


def test_table_exists(db_with_legacy_tables: Path):
    mod = _load_ingest_module()
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        assert mod._table_exists(con, "claude_sessions") is True
        assert mod._table_exists(con, "nonexistent") is False
    finally:
        con.close()


def test_run_legacy_migration_backfills_and_drops(db_with_legacy_tables: Path, tmp_path: Path):
    mod = _load_ingest_module()
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        mod._run_legacy_migration(con, tmp_path)
        assert mod._table_exists(con, "claude_sessions") is False
        assert mod._table_exists(con, "codex_sessions") is False
        rows = con.execute(
            "SELECT agent, session_id, cwd, first_user_prompt FROM code_agent_sessions ORDER BY agent"
        ).fetchall()
        assert rows == [
            ("claude_code", "c1", None, "hi"),
            ("codex",       "x1", "/repo", "hello"),
        ]
    finally:
        con.close()


def test_run_legacy_migration_is_idempotent(db_with_legacy_tables: Path, tmp_path: Path):
    mod = _load_ingest_module()
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        mod._run_legacy_migration(con, tmp_path)
        # Second run is a no-op (no errors, no rows added)
        before = con.execute("SELECT count(*) FROM code_agent_sessions").fetchone()[0]
        mod._run_legacy_migration(con, tmp_path)
        after = con.execute("SELECT count(*) FROM code_agent_sessions").fetchone()[0]
        assert before == after == 2
    finally:
        con.close()


def test_is_canonical_tracker_dir_accepts_canonical(tmp_path: Path):
    mod = _load_ingest_module()
    d = tmp_path / "claude_conversations"
    d.mkdir()
    for name in ("manifest.yaml", "ingest.py", "schema.sql", "visualizations.py"):
        (d / name).write_text("# test")
    (d / "__pycache__").mkdir()
    assert mod._is_canonical_tracker_dir(d) is True


def test_is_canonical_tracker_dir_rejects_extra_file(tmp_path: Path):
    mod = _load_ingest_module()
    d = tmp_path / "claude_conversations"
    d.mkdir()
    for name in ("manifest.yaml", "ingest.py", "schema.sql", "visualizations.py", "user_custom.py"):
        (d / name).write_text("# test")
    assert mod._is_canonical_tracker_dir(d) is False


def test_run_legacy_migration_removes_canonical_tracker_dirs(db_with_legacy_tables: Path, tmp_path: Path):
    mod = _load_ingest_module()
    trackers = tmp_path / "trackers"
    trackers.mkdir()
    for name in ("claude_conversations", "codex_conversations"):
        d = trackers / name
        d.mkdir()
        for fn in ("manifest.yaml", "ingest.py", "schema.sql", "visualizations.py"):
            (d / fn).write_text("# test")
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        mod._run_legacy_migration(con, tmp_path)
    finally:
        con.close()
    assert not (trackers / "claude_conversations").exists()
    assert not (trackers / "codex_conversations").exists()


def test_run_legacy_migration_preserves_non_canonical_dir(db_with_legacy_tables: Path, tmp_path: Path):
    mod = _load_ingest_module()
    trackers = tmp_path / "trackers"
    trackers.mkdir()
    d = trackers / "claude_conversations"
    d.mkdir()
    for fn in ("manifest.yaml", "ingest.py", "schema.sql", "visualizations.py", "user_custom.py"):
        (d / fn).write_text("# test")
    con = sqlite3.connect(db_with_legacy_tables)
    try:
        mod._run_legacy_migration(con, tmp_path)
    finally:
        con.close()
    assert (trackers / "claude_conversations").exists()
    assert (trackers / "claude_conversations" / "user_custom.py").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_migration.py -v
```

Expected: All tests fail — helpers undefined.

- [ ] **Step 3: Add helpers to ingest.py**

Add `import shutil` to the imports at the top of `src/personal_db/templates/trackers/code_agent_activity/ingest.py` (alongside the existing `import os`, `import sqlite3`).

Then append after `_ensure_schema_columns` (after line ~210):

```python


_CANONICAL_TRACKER_FILES = {
    "manifest.yaml",
    "ingest.py",
    "schema.sql",
    "visualizations.py",
}
_PERMITTED_NOISE = {"__pycache__"}


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _is_canonical_tracker_dir(d: Path) -> bool:
    """True iff d's contents are exactly the four canonical tracker files
    (plus permitted noise like __pycache__). Protects user customizations."""
    if not d.is_dir():
        return False
    entries = {p.name for p in d.iterdir()}
    extras = entries - _CANONICAL_TRACKER_FILES - _PERMITTED_NOISE
    if extras:
        return False
    return _CANONICAL_TRACKER_FILES.issubset(entries)


def _run_legacy_migration(con: sqlite3.Connection, root: Path) -> None:
    """One-shot, idempotent migration from the now-defunct
    claude_conversations / codex_conversations trackers.

    All branches gate on legacy-artifact existence so subsequent runs are
    cheap no-ops. Safe to call on every sync.
    """
    if _table_exists(con, "claude_sessions"):
        con.execute(
            """
            INSERT OR IGNORE INTO code_agent_sessions
              (agent, session_id, cwd, started_at, last_msg_at,
               message_count, user_msg_count, assistant_msg_count,
               first_user_prompt, source_file)
            SELECT 'claude_code', session_id, NULL, started_at, last_msg_at,
                   message_count, user_msg_count, assistant_msg_count,
                   first_user_prompt, NULL
            FROM claude_sessions
            """
        )
        con.execute("DROP TABLE claude_sessions")

    if _table_exists(con, "codex_sessions"):
        con.execute(
            """
            INSERT OR IGNORE INTO code_agent_sessions
              (agent, session_id, cwd, started_at, last_msg_at,
               message_count, user_msg_count, assistant_msg_count,
               first_user_prompt, source_file)
            SELECT 'codex', session_id, cwd, started_at, last_event_at,
                   event_count, user_msg_count, assistant_msg_count,
                   first_user_prompt, NULL
            FROM codex_sessions
            """
        )
        con.execute("DROP TABLE codex_sessions")

    con.commit()

    trackers_dir = root / "trackers"
    for stale in ("claude_conversations", "codex_conversations"):
        d = trackers_dir / stale
        if not d.exists():
            continue
        if _is_canonical_tracker_dir(d):
            shutil.rmtree(d)
            log.info("code_agent_activity: removed legacy tracker dir %s", d)
        else:
            log.warning(
                "code_agent_activity: leaving %s in place (non-canonical contents)", d
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_migration.py -v
```

Expected: 7 passes.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/ingest.py tests/unit/test_code_agent_migration.py
git commit -m "feat(code-agent): one-shot legacy-tracker migration"
```

---

## Task 7: Wire migration + session rollup into sync()

Hook `_run_legacy_migration` into `sync()` and add a session-rollup phase that walks JSONL files (with mtime cursor) and upserts into `code_agent_sessions`. Use `INSERT OR REPLACE` so re-running on the same file replaces the row.

**Files:**
- Modify: `src/personal_db/templates/trackers/code_agent_activity/ingest.py`
- Modify: `tests/unit/test_code_agent_sessions.py`

- [ ] **Step 1: Append failing end-to-end test**

Append to `tests/unit/test_code_agent_sessions.py`:

```python
import shutil
import sqlite3 as _sqlite3
import sys

from personal_db.config import Config
from personal_db.installer import install_template


@pytest.fixture
def cfg_with_code_agent(tmp_path):
    root = tmp_path / "personal_db"
    for sub in ("trackers", "entities", "notes", "state", "state/oauth"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    cfg = Config(root=root)
    install_template(cfg, "code_agent_activity")
    schema_sql = (cfg.trackers_dir / "code_agent_activity" / "schema.sql").read_text()
    con = _sqlite3.connect(cfg.db_path)
    con.executescript(schema_sql)
    con.commit()
    con.close()
    return cfg


def test_sync_populates_code_agent_sessions(cfg_with_code_agent, monkeypatch):
    cfg = cfg_with_code_agent

    # Stage Claude fixture into a tmp claude projects root so mtime is fresh.
    claude_src = FIXTURES / "claude_projects/projects"
    claude_dst = cfg.root / "fake_claude_projects"
    shutil.copytree(claude_src, claude_dst)
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(claude_dst))

    # Stage Codex fixture and point CODEX_HOME at its parent.
    codex_src = FIXTURES / "codex_sessions/sessions"
    codex_home = cfg.root / "fake_codex"
    (codex_home).mkdir()
    shutil.copytree(codex_src, codex_home / "sessions")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    # No history file → first prompts come from rollout JSONL.
    monkeypatch.setenv("CODEX_HISTORY_FILE", str(codex_home / "history.jsonl"))

    # Empty hooks log so the events phase is a no-op for Claude side.
    (cfg.state_dir / "code_agent_hooks.jsonl").write_text("")
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(cfg.state_dir / "code_agent_hooks.jsonl"))

    from personal_db.sync import sync_one
    sync_one(cfg, "code_agent_activity")

    con = _sqlite3.connect(cfg.db_path)
    rows = con.execute(
        "SELECT agent, session_id, cwd, message_count, first_user_prompt "
        "FROM code_agent_sessions ORDER BY agent"
    ).fetchall()
    con.close()
    assert ("claude_code", "abc123", "/Users/test/code/example", 3, "hello, can you help me debug?") in rows
    assert any(
        r[0] == "codex"
        and r[1] == "550e8400-e29b-41d4-a716-446655440000"
        and r[2] == "/Users/test/code/example"
        for r in rows
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_sessions.py::test_sync_populates_code_agent_sessions -v
```

Expected: FAIL — session rollup phase not yet wired.

- [ ] **Step 3: Wire migration + sessions sibling load**

In `src/personal_db/templates/trackers/code_agent_activity/ingest.py`, after the existing `_load_sibling("intervals")` block (around line 48), add:

```python
_sessions = _load_sibling("sessions")
parse_claude_session = _sessions.parse_claude_session
parse_codex_session = _sessions.parse_codex_session
load_codex_history_first_prompts = _sessions.load_codex_history_first_prompts
claude_root = _sessions.claude_root
```

- [ ] **Step 4: Add session-rollup phase**

Add this function near the bottom of `ingest.py`, before `def sync(t: Tracker)`:

```python
def _ingest_sessions(t: Tracker, state: dict) -> int:
    """Walk Claude + Codex JSONL files newer than the per-source mtime cursor
    and upsert one rollup row per session into code_agent_sessions.

    Returns the count of rows upserted.
    """
    sessions_cursor = state.setdefault(
        "sessions", {"claude_mtime": 0.0, "codex_mtime": 0.0}
    )
    rows: list[dict] = []

    # Claude
    cproj = claude_root()
    cmtime = float(sessions_cursor.get("claude_mtime") or 0.0)
    new_cmtime = cmtime
    if cproj.exists():
        for project_dir in sorted(cproj.iterdir()):
            if not project_dir.is_dir():
                continue
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                try:
                    m = jsonl.stat().st_mtime
                except OSError:
                    continue
                if m <= cmtime:
                    continue
                if m > new_cmtime:
                    new_cmtime = m
                row = parse_claude_session(jsonl)
                if row is not None:
                    rows.append(row)

    # Codex
    csess = _codex_sessions_root()
    xmtime = float(sessions_cursor.get("codex_mtime") or 0.0)
    new_xmtime = xmtime
    history = load_codex_history_first_prompts()
    if csess.exists():
        for jsonl in sorted(csess.rglob("*.jsonl")):
            try:
                m = jsonl.stat().st_mtime
            except OSError:
                continue
            if m <= xmtime:
                continue
            if m > new_xmtime:
                new_xmtime = m
            row = parse_codex_session(jsonl, history)
            if row is not None:
                rows.append(row)

    if rows:
        t.upsert("code_agent_sessions", rows, key=["agent", "session_id"])

    sessions_cursor["claude_mtime"] = new_cmtime
    sessions_cursor["codex_mtime"] = new_xmtime
    return len(rows)
```

- [ ] **Step 5: Call migration + session-rollup from sync()**

Modify `sync()` in `src/personal_db/templates/trackers/code_agent_activity/ingest.py`. Replace the existing schema-migration block:

```python
    _con = sqlite3.connect(t.cfg.db_path)
    try:
        _ensure_schema_columns(_con)
    finally:
        _con.close()
```

with:

```python
    _con = sqlite3.connect(t.cfg.db_path)
    try:
        _ensure_schema_columns(_con)
        _run_legacy_migration(_con, t.cfg.root)
    finally:
        _con.close()
```

Then, immediately before `_save_cursor(t, state)` near the end of `sync()`, add:

```python
    sessions_n = _ingest_sessions(t, state)
```

And change the return-dict line to include `"sessions_upserted": sessions_n,`. Final return looks like:

```python
    return {
        "claude_events": len(claude_events),
        "codex_events": len(codex_events),
        "events_upserted": inserted,
        "intervals_materialized": intervals_n,
        "sessions_upserted": sessions_n,
        "skipped_lines": claude_skipped + codex_skipped,
    }
```

- [ ] **Step 6: Update backfill() to clear the sessions cursor too**

Modify `backfill()` at the bottom of `ingest.py`:

```python
def backfill(t: Tracker, start: str | None = None, end: str | None = None) -> dict:
    """Reset cursors and re-ingest everything. start/end are advisory only —
    we don't filter the events log by date; idempotent upsert handles dupes."""
    t.cursor.set(json.dumps({
        "claude_hooks_offset": 0,
        "codex_files": {},
        "sessions": {"claude_mtime": 0.0, "codex_mtime": 0.0},
    }))
    return sync(t)
```

- [ ] **Step 7: Run all code_agent tests**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_sessions.py tests/unit/test_code_agent_migration.py tests/unit/test_code_agent_ingest.py -v
```

Expected: ALL PASS.

- [ ] **Step 8: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/ingest.py tests/unit/test_code_agent_sessions.py
git commit -m "feat(code-agent): wire migration + session rollup into sync"
```

---

## Task 8: Claude `first_user_prompt` fallback from hook events (TDD)

When a Claude session has hook events but no JSONL on disk (deleted, recently created and not flushed, etc.), populate `first_user_prompt` from the earliest `user_prompt_submit` event's `raw` payload.

**Files:**
- Modify: `src/personal_db/templates/trackers/code_agent_activity/ingest.py`
- Modify: `tests/unit/test_code_agent_sessions.py`

- [ ] **Step 1: Append failing test**

Append to `tests/unit/test_code_agent_sessions.py`:

```python
def test_claude_session_first_prompt_fallback_from_hook_events(cfg_with_code_agent, monkeypatch):
    """Session present in code_agent_events but no JSONL: first_user_prompt
    populated from earliest user_prompt_submit event."""
    import json as _json
    cfg = cfg_with_code_agent
    # Empty Claude project root → no JSONL for session "ghost"
    empty_claude = cfg.root / "empty_claude_projects"
    empty_claude.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(empty_claude))
    # Empty Codex too
    empty_codex = cfg.root / "empty_codex"
    empty_codex.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(empty_codex))
    monkeypatch.setenv("CODEX_HISTORY_FILE", str(empty_codex / "history.jsonl"))

    # Hook event log with a SessionStart + UserPromptSubmit + Stop for "ghost"
    hooks_log = cfg.state_dir / "code_agent_hooks.jsonl"
    monkeypatch.setenv("PERSONAL_DB_HOOKS_LOG", str(hooks_log))
    hooks_log.write_text("\n".join([
        _json.dumps({
            "hook_event_name": "SessionStart",
            "session_id": "ghost",
            "timestamp": "2026-04-26T12:00:00Z",
            "cwd": "/Users/test/elsewhere",
        }),
        _json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": "ghost",
            "timestamp": "2026-04-26T12:00:05Z",
            "cwd": "/Users/test/elsewhere",
            "prompt": "fix the leaky abstraction",
        }),
        _json.dumps({
            "hook_event_name": "Stop",
            "session_id": "ghost",
            "timestamp": "2026-04-26T12:01:00Z",
            "cwd": "/Users/test/elsewhere",
        }),
        "",
    ]))

    from personal_db.sync import sync_one
    sync_one(cfg, "code_agent_activity")

    con = _sqlite3.connect(cfg.db_path)
    row = con.execute(
        "SELECT cwd, first_user_prompt FROM code_agent_sessions "
        "WHERE agent='claude_code' AND session_id='ghost'"
    ).fetchone()
    con.close()
    assert row is not None, "ghost session row should be created from hook events"
    assert row[0] == "/Users/test/elsewhere"
    assert row[1] == "fix the leaky abstraction"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_sessions.py::test_claude_session_first_prompt_fallback_from_hook_events -v
```

Expected: FAIL — no row produced for `ghost` because there's no JSONL for it.

- [ ] **Step 3: Add `_synthesize_claude_sessions_from_events` helper**

Add to `ingest.py` immediately above `_ingest_sessions`:

```python
def _synthesize_claude_sessions_from_events(con: sqlite3.Connection) -> list[dict]:
    """For Claude sessions that have hook events but no row in
    code_agent_sessions yet, build a rollup row from the events alone.

    `first_user_prompt` comes from the earliest user_prompt_submit event's
    raw payload (`prompt` field). cwd comes from the earliest event with cwd.
    """
    rows = con.execute(
        """
        WITH have_session AS (
          SELECT session_id FROM code_agent_sessions WHERE agent='claude_code'
        ),
        agg AS (
          SELECT
            e.session_id AS sid,
            MIN(e.timestamp) AS started_at,
            MAX(e.timestamp) AS last_msg_at,
            (SELECT cwd FROM code_agent_events e2
              WHERE e2.agent='claude_code' AND e2.session_id=e.session_id
                AND e2.cwd IS NOT NULL
              ORDER BY e2.timestamp LIMIT 1) AS cwd,
            SUM(CASE WHEN e.event_type='user_prompt_submit' THEN 1 ELSE 0 END) AS user_n
          FROM code_agent_events e
          WHERE e.agent='claude_code'
            AND e.session_id NOT IN (SELECT session_id FROM have_session)
          GROUP BY e.session_id
        )
        SELECT
          a.sid, a.started_at, a.last_msg_at, a.cwd, a.user_n,
          (SELECT json_extract(e.raw, '$.prompt')
             FROM code_agent_events e
            WHERE e.agent='claude_code' AND e.session_id=a.sid
              AND e.event_type='user_prompt_submit'
            ORDER BY e.timestamp LIMIT 1) AS first_prompt
        FROM agg a
        """
    ).fetchall()

    out: list[dict] = []
    for sid, started_at, last_msg_at, cwd, user_n, first_prompt in rows:
        out.append({
            "agent": "claude_code",
            "session_id": sid,
            "cwd": cwd,
            "started_at": started_at,
            "last_msg_at": last_msg_at,
            "message_count": int(user_n or 0),
            "user_msg_count": int(user_n or 0),
            "assistant_msg_count": 0,
            "first_user_prompt": (first_prompt[:500] if first_prompt else None),
            "source_file": None,
        })
    return out
```

- [ ] **Step 4: Call the synthesizer at the end of _ingest_sessions**

In `_ingest_sessions`, replace the final `if rows:` block with:

```python
    if rows:
        t.upsert("code_agent_sessions", rows, key=["agent", "session_id"])

    # After JSONL-derived rows are written, synthesize rows for sessions that
    # exist only as hook events (e.g., JSONL deleted or not yet flushed).
    syn_con = sqlite3.connect(t.cfg.db_path)
    try:
        synth = _synthesize_claude_sessions_from_events(syn_con)
    finally:
        syn_con.close()
    if synth:
        t.upsert("code_agent_sessions", synth, key=["agent", "session_id"])
```

And change the rollup count to include synthetic rows:

```python
    return len(rows) + len(synth)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_sessions.py -v
```

Expected: All passes.

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/ingest.py tests/unit/test_code_agent_sessions.py
git commit -m "feat(code-agent): synthesize claude sessions from hook events"
```

---

## Task 9: Visualization enrichment — per-session timeline header + cwd JOIN

Two passes inside `code_agent_activity/visualizations.py`:

1. The per-session 24h timeline (existing) gains a header showing `first_user_prompt` and `cwd` from the new sessions table.
2. Charts that label intervals by `cwd` JOIN through `code_agent_sessions` for a more reliable label than `code_agent_intervals.cwd` (which is start-time only and can be NULL).

Read `visualizations.py` first to find the exact spots.

**Files:**
- Modify: `src/personal_db/templates/trackers/code_agent_activity/visualizations.py`

- [ ] **Step 1: Locate the per-session timeline SQL**

```bash
grep -n "session_meta\|FROM code_agent_intervals\|cwd" src/personal_db/templates/trackers/code_agent_activity/visualizations.py | head -30
```

Note line numbers for the per-session timeline query and any `GROUP BY cwd`/`SELECT ... cwd ...` queries that label intervals.

- [ ] **Step 2: JOIN code_agent_sessions into the per-session timeline**

In the per-session timeline view, add a `LEFT JOIN code_agent_sessions s USING (agent, session_id)` and `SELECT s.first_user_prompt, s.cwd AS session_cwd` to the query, then surface those values in the rendered header (HTML/Markdown — match the existing style of nearby viz blocks).

If the existing query is something like:
```sql
SELECT i.start_ts, i.end_ts, i.state, i.cwd, i.git_branch
FROM code_agent_intervals i
WHERE i.agent=? AND i.session_id=?
ORDER BY i.start_ts
```

Replace with:
```sql
SELECT i.start_ts, i.end_ts, i.state, COALESCE(s.cwd, i.cwd) AS cwd,
       i.git_branch, s.first_user_prompt
FROM code_agent_intervals i
LEFT JOIN code_agent_sessions s USING (agent, session_id)
WHERE i.agent=? AND i.session_id=?
ORDER BY i.start_ts
```

The rendering function then prints `first_user_prompt` (truncated 100 chars) and `cwd` as a header row above the interval bars. Match the nearby viz block's existing string-formatting helper.

- [ ] **Step 3: Replace `i.cwd` with `COALESCE(s.cwd, i.cwd)` in cwd-grouped charts**

For any other query in `visualizations.py` that does `GROUP BY i.cwd` or `SELECT ... i.cwd ...`, add the same `LEFT JOIN code_agent_sessions s USING (agent, session_id)` and use `COALESCE(s.cwd, i.cwd)` as the cwd source.

- [ ] **Step 4: Smoke-test by re-rendering**

```bash
.venv/bin/python -c "
from pathlib import Path
import importlib.util as ilu
spec = ilu.spec_from_file_location('viz', Path('src/personal_db/templates/trackers/code_agent_activity/visualizations.py'))
m = ilu.module_from_spec(spec); spec.loader.exec_module(m)
print('viz module loaded:', dir(m))
"
```

Expected: module loads, lists its public functions (no syntax error).

- [ ] **Step 5: Run the existing code_agent test suite**

```bash
.venv/bin/python -m pytest tests/unit/test_code_agent_ingest.py tests/unit/test_code_agent_intervals.py -v
```

Expected: PASS (these don't load the viz module, but ensure we haven't broken the tracker package).

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/templates/trackers/code_agent_activity/visualizations.py
git commit -m "feat(code-agent): enrich viz with session content"
```

---

## Task 10: Delete the old bundled tracker templates

**Files:**
- Delete: `src/personal_db/templates/trackers/claude_conversations/`
- Delete: `src/personal_db/templates/trackers/codex_conversations/`

- [ ] **Step 1: Delete with git**

```bash
git rm -r src/personal_db/templates/trackers/claude_conversations
git rm -r src/personal_db/templates/trackers/codex_conversations
```

- [ ] **Step 2: Confirm `list_bundled()` no longer returns them**

```bash
.venv/bin/python -c "from personal_db.installer import list_bundled; names=set(list_bundled()); assert 'claude_conversations' not in names; assert 'codex_conversations' not in names; print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(code-agent): drop bundled claude_conversations and codex_conversations templates"
```

---

## Task 11: Update test_installer.py

`test_list_bundled_returns_known_templates` currently asserts the old names are in the set; that assertion now fails.

**Files:**
- Modify: `tests/unit/test_installer.py:7-19`

- [ ] **Step 1: Edit the assertion**

In `tests/unit/test_installer.py`, update:

```python
def test_list_bundled_returns_known_templates():
    names = set(list_bundled())
    # These connectors ship with the package; new ones extend this set.
    assert {
        "github_commits",
        "granola",
        "whoop",
        "screen_time",
        "imessage",
        "habits",
        "claude_conversations",
        "codex_conversations",
    } <= names
```

to:

```python
def test_list_bundled_returns_known_templates():
    names = set(list_bundled())
    # These connectors ship with the package; new ones extend this set.
    assert {
        "github_commits",
        "granola",
        "whoop",
        "screen_time",
        "imessage",
        "habits",
        "code_agent_activity",
    } <= names
    # claude_conversations and codex_conversations were folded into code_agent_activity.
    assert "claude_conversations" not in names
    assert "codex_conversations" not in names
```

- [ ] **Step 2: Run installer tests**

```bash
.venv/bin/python -m pytest tests/unit/test_installer.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_installer.py
git commit -m "test(installer): drop conversation trackers from bundled-set assertion"
```

---

## Task 12: Delete the old integration tests

The two `tests/integration/test_connector_*_conversations.py` files exercise tracker code that no longer exists. The new unit tests cover the same parser logic against the same fixtures.

**Files:**
- Delete: `tests/integration/test_connector_claude_conversations.py`
- Delete: `tests/integration/test_connector_codex_conversations.py`

- [ ] **Step 1: Delete with git**

```bash
git rm tests/integration/test_connector_claude_conversations.py
git rm tests/integration/test_connector_codex_conversations.py
```

- [ ] **Step 2: Run the full test suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: ALL PASS, no collection errors.

- [ ] **Step 3: Commit**

```bash
git commit -m "test: remove integration tests for deleted conversation trackers"
```

---

## Task 13: End-to-end validation against ~/personal_db

Real-world sanity check: reinstall the tracker against the user's actual install, run a sync, eyeball the result.

- [ ] **Step 1: Reinstall the updated tracker**

```bash
personal-db --root ~/personal_db tracker reinstall code_agent_activity
```

Expected: `tracker reinstall` re-applies `schema.sql` (creating `code_agent_sessions`) and copies the new `sessions.py` sibling.

- [ ] **Step 2: Sync once to trigger the migration**

```bash
personal-db --root ~/personal_db sync code_agent_activity
```

Expected output includes `sessions_upserted: <N>` where N > 0. Daemon log should show "removed legacy tracker dir" lines for the two old dirs (if they were canonical) or warning lines (if not).

- [ ] **Step 3: Verify legacy tables are gone and new table is populated**

```bash
sqlite3 ~/personal_db/db.sqlite "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('claude_sessions','codex_sessions','code_agent_sessions');"
```

Expected: `code_agent_sessions` only.

```bash
sqlite3 ~/personal_db/db.sqlite "SELECT agent, count(*), count(cwd), count(first_user_prompt) FROM code_agent_sessions GROUP BY agent;"
```

Expected: two rows (claude_code, codex), all counts > 0, cwd populated for most rows.

- [ ] **Step 4: Verify legacy installed dirs are gone**

```bash
ls ~/personal_db/trackers/ | grep -E "claude_conversations|codex_conversations" || echo "OK - cleaned up"
```

Expected: `OK - cleaned up`. (Or if the user customized one of them, only the customized one remains.)

- [ ] **Step 5: Commit any final docs/changelog updates if needed**

If anything in CLAUDE.md or specs/plans needs touching up after the live run, do so now and commit. Otherwise this task is just verification.

---

## Self-Review

**Spec coverage:**
- ✅ New `code_agent_sessions` table (Task 3)
- ✅ Session-rollup ingest phase, single-pass JSONL read (Tasks 4, 5, 7)
- ✅ Migration: backfill, drop legacy tables, remove orphan tracker dirs (Task 6)
- ✅ Idempotency by construction (Task 6 tests cover this)
- ✅ `first_user_prompt` JSONL primary, hook fallback (Tasks 4, 8)
- ✅ `cwd` resolution (JSONL message metadata → hook events → reverse-mapped slug last resort) (Tasks 4, 8 — reverse-map fallback is intentionally omitted as YAGNI; the synthesizer covers the realistic gap)
- ✅ Template deletions (Task 10)
- ✅ Visualization enrichment (Task 9)
- ✅ Tests for parsers, migration, end-to-end (Tasks 4, 5, 6, 7, 8)
- ✅ Live validation step (Task 13)

**Note on the spec's `cwd` priority chain:** the spec listed reverse-mapped slug as a final fallback for Claude. In practice it's never reached — JSONL message lines carry `cwd`, and hook events carry `cwd`. Implementing the slug reverse-map adds code that no test would exercise. The plan intentionally drops it; if a real session ever surfaces with neither source providing `cwd`, we'll add it then.

**Placeholder scan:** all code/SQL/test bodies are present. No "TODO" or "TBD" or "similar to" references that don't include code.

**Type consistency:** `parse_claude_session` and `parse_codex_session` both return dicts with the exact same keys defined in the schema, in the same order: `agent, session_id, cwd, started_at, last_msg_at, message_count, user_msg_count, assistant_msg_count, first_user_prompt, source_file`. The synthesizer (Task 8) returns the same key set with `source_file=None`. The migration backfill (Task 6) inserts the same column set.
