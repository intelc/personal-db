# Granola Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bundled `granola` tracker that pulls Granola meeting documents (notes + speaker-attributed transcripts) into `personal_db`, using the Granola desktop app's stored access token directly (no token refresh).

**Architecture:** Standard 4-file bundled tracker at `src/personal_db/templates/trackers/granola/`. `ingest.py` reads `~/Library/Application Support/Granola/supabase.json` on every sync, POSTs to `api.granola.ai`, pages newest-first with an `updated_at` cursor, and stores rows in a `granola_documents` table.

**Tech Stack:** Python 3, `requests`, pytest. Reuses the existing `personal_db.tracker.Tracker` API (`t.cfg`, `t.cursor`, `t.log`, `t.upsert`). No new framework code.

**Spec:** `docs/superpowers/specs/2026-05-02-granola-tracker-design.md`

---

## File Structure

**Create (all under `src/personal_db/templates/trackers/granola/`):**

- `__init__.py` — empty marker.
- `manifest.yaml` — tracker declaration (setup_steps, schedule, schema).
- `schema.sql` — `granola_documents` DDL + indexes.
- `ingest.py` — `sync(t)` and `backfill(t, start, end)` entrypoints, plus private helpers (`_read_access_token`, `_prosemirror_to_text`, `_flatten`, `_list_documents`, `_fetch_transcript`).
- `visualizations.py` — `activity_calendar` + `recent` views, ported from omi.

**Create:**

- `tests/unit/test_granola_tracker.py` — unit tests for the helpers and the sync/backfill cursor walks.
- `tests/unit/fixtures/granola/supabase_string_form.json` — fixture: `workos_tokens` as JSON-encoded string.
- `tests/unit/fixtures/granola/supabase_dict_form.json` — fixture: `workos_tokens` as dict.

**Modify:**

- `tests/unit/test_smoke.py` — add `granola` to the bundled-tracker assertion (if such an assertion exists; verify in Task 1).

---

## Task 1: Manifest, schema, and empty stubs

**Files:**
- Create: `src/personal_db/templates/trackers/granola/__init__.py`
- Create: `src/personal_db/templates/trackers/granola/manifest.yaml`
- Create: `src/personal_db/templates/trackers/granola/schema.sql`
- Create: `src/personal_db/templates/trackers/granola/ingest.py` (stub)
- Create: `src/personal_db/templates/trackers/granola/visualizations.py` (stub)
- Test: `tests/unit/test_installer.py` (add an assertion) and/or `tests/unit/test_smoke.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_installer.py` (after `test_list_bundled_returns_known_templates`):

```python
def test_granola_is_bundled():
    names = set(list_bundled())
    assert "granola" in names


def test_granola_manifest_loads():
    from pathlib import Path
    from personal_db.manifest import load_manifest

    here = Path(__file__).resolve().parents[2]
    m = load_manifest(here / "src/personal_db/templates/trackers/granola/manifest.yaml")
    assert m.name == "granola"
    assert m.permission_type == "api_key"
    assert "granola_documents" in m.schema.tables
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_installer.py::test_granola_is_bundled tests/unit/test_installer.py::test_granola_manifest_loads -v`
Expected: FAIL — granola directory does not exist.

- [ ] **Step 3: Create the empty marker file**

Create `src/personal_db/templates/trackers/granola/__init__.py` with no content (empty file).

- [ ] **Step 4: Create `manifest.yaml`**

```yaml
name: granola
description: Granola meeting notes — title, content, transcript, participants
permission_type: api_key
setup_steps:
  - type: instructions
    text: |
      Granola tokens are read from the desktop app on every sync.

        1. Install Granola: https://www.granola.ai
        2. Sign in to the desktop app
        3. Keep the app installed and open Granola occasionally so its token stays fresh

      No keys to paste. If syncs start failing with "access token expired",
      just open the Granola desktop app and re-run.
schedule:
  every: 30m
time_column: started_at
granularity: event
schema:
  tables:
    granola_documents:
      columns:
        id:               {type: TEXT,    semantic: "Granola document id, primary key"}
        started_at:       {type: TEXT,    semantic: "ISO-8601 meeting start (UTC), from first transcript utterance, falls back to created_at"}
        finished_at:      {type: TEXT,    semantic: "ISO-8601 meeting end (UTC), from last transcript utterance"}
        duration_seconds: {type: INTEGER, semantic: "wall-clock duration"}
        title:            {type: TEXT,    semantic: "Granola-generated meeting title"}
        overview:         {type: TEXT,    semantic: "plaintext extract of content (Granola notes)"}
        content:          {type: TEXT,    semantic: "raw Granola notes JSON (ProseMirror-style)"}
        transcript:       {type: TEXT,    semantic: "speaker-attributed transcript, one line per utterance: '[me] ...' / '[them] ...'"}
        participants:     {type: TEXT,    semantic: "JSON array of meeting participants"}
        created_at:       {type: TEXT,    semantic: "ISO-8601 doc creation (≠ meeting start)"}
        updated_at:       {type: TEXT,    semantic: "ISO-8601 last edit"}
related_entities: []
```

- [ ] **Step 5: Create `schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS granola_documents (
  id               TEXT PRIMARY KEY,
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  duration_seconds INTEGER,
  title            TEXT,
  overview         TEXT,
  content          TEXT,
  transcript       TEXT,
  participants     TEXT,
  created_at       TEXT,
  updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_granola_documents_started_at
  ON granola_documents(started_at);
CREATE INDEX IF NOT EXISTS idx_granola_documents_updated_at
  ON granola_documents(updated_at);
```

- [ ] **Step 6: Create `ingest.py` stub**

```python
"""Granola tracker — pulls meeting docs and transcripts.

Auth: reads the access token directly from the Granola desktop app's local
supabase.json on every sync. We do not refresh; if the token is stale, the
user must open the Granola desktop app to refresh it.
"""

from personal_db.tracker import Tracker


def sync(t: Tracker) -> None:
    raise NotImplementedError


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    raise NotImplementedError
```

- [ ] **Step 7: Create `visualizations.py` stub**

```python
"""Visualizations for the granola tracker."""


def list_visualizations() -> list[dict]:
    return []
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_installer.py::test_granola_is_bundled tests/unit/test_installer.py::test_granola_manifest_loads -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/personal_db/templates/trackers/granola/ tests/unit/test_installer.py
git commit -m "feat(granola): scaffold tracker — manifest, schema, stubs"
```

---

## Task 2: `_read_access_token` — token detection from supabase.json

**Files:**
- Modify: `src/personal_db/templates/trackers/granola/ingest.py`
- Create: `tests/unit/test_granola_tracker.py`
- Create: `tests/unit/fixtures/granola/supabase_string_form.json`
- Create: `tests/unit/fixtures/granola/supabase_dict_form.json`

- [ ] **Step 1: Create fixture (string form of `workos_tokens`)**

Create `tests/unit/fixtures/granola/supabase_string_form.json`:

```json
{
  "https://abc123.supabase.co": {
    "session": {
      "workos_tokens": "{\"access_token\":\"AT_STRING_FORM\",\"refresh_token\":\"RT_STRING_FORM\"}"
    }
  }
}
```

- [ ] **Step 2: Create fixture (dict form of `workos_tokens`)**

Create `tests/unit/fixtures/granola/supabase_dict_form.json`:

```json
{
  "https://abc123.supabase.co": {
    "session": {
      "workos_tokens": {
        "access_token": "AT_DICT_FORM",
        "refresh_token": "RT_DICT_FORM"
      }
    }
  }
}
```

- [ ] **Step 3: Write failing tests**

Create `tests/unit/test_granola_tracker.py`:

```python
"""Unit tests for the granola tracker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_db.templates.trackers.granola import ingest as granola_ingest

FIXTURES = Path(__file__).parent / "fixtures" / "granola"


def test_read_access_token_string_form():
    token = granola_ingest._read_access_token(FIXTURES / "supabase_string_form.json")
    assert token == "AT_STRING_FORM"


def test_read_access_token_dict_form():
    token = granola_ingest._read_access_token(FIXTURES / "supabase_dict_form.json")
    assert token == "AT_DICT_FORM"


def test_read_access_token_missing_file(tmp_path):
    with pytest.raises(RuntimeError, match="Granola desktop app not detected"):
        granola_ingest._read_access_token(tmp_path / "does_not_exist.json")


def test_read_access_token_empty_token(tmp_path):
    p = tmp_path / "supabase.json"
    p.write_text(json.dumps({
        "session": {"workos_tokens": {"access_token": "", "refresh_token": "r"}}
    }))
    with pytest.raises(RuntimeError, match="access token not found"):
        granola_ingest._read_access_token(p)


def test_read_access_token_no_workos_tokens(tmp_path):
    p = tmp_path / "supabase.json"
    p.write_text(json.dumps({"session": {"other_key": "value"}}))
    with pytest.raises(RuntimeError, match="access token not found"):
        granola_ingest._read_access_token(p)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v`
Expected: FAIL — `_read_access_token` does not exist.

- [ ] **Step 5: Implement `_read_access_token`**

Update `src/personal_db/templates/trackers/granola/ingest.py`:

```python
"""Granola tracker — pulls meeting docs and transcripts.

Auth: reads the access token directly from the Granola desktop app's local
supabase.json on every sync. We do not refresh; if the token is stale, the
user must open the Granola desktop app to refresh it.
"""

from __future__ import annotations

import json
from pathlib import Path

from personal_db.tracker import Tracker

SUPABASE_PATH = Path.home() / "Library/Application Support/Granola/supabase.json"


def _extract_workos_access_token(node) -> str | None:
    """Walk a JSON tree looking for workos_tokens.access_token.

    `workos_tokens` may be a JSON-encoded string or a dict — handle both.
    Returns the first non-empty access_token found, or None.
    """
    if isinstance(node, dict):
        wt = node.get("workos_tokens")
        if isinstance(wt, str):
            try:
                wt = json.loads(wt)
            except json.JSONDecodeError:
                wt = None
        if isinstance(wt, dict):
            tok = wt.get("access_token") or ""
            if tok:
                return tok
        for value in node.values():
            tok = _extract_workos_access_token(value)
            if tok:
                return tok
    elif isinstance(node, list):
        for item in node:
            tok = _extract_workos_access_token(item)
            if tok:
                return tok
    return None


def _read_access_token(path: Path = SUPABASE_PATH) -> str:
    """Read the current Granola access token from the desktop app's local store.

    Raises RuntimeError with a user-facing instruction when the file is missing
    or no token can be extracted.
    """
    if not path.exists():
        raise RuntimeError(
            f"Granola desktop app not detected at {path}. "
            "Install Granola and sign in."
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Granola supabase.json is not valid JSON: {e}") from e

    token = _extract_workos_access_token(data)
    if not token:
        raise RuntimeError(
            "Granola access token not found in supabase.json. "
            "Sign in to the Granola desktop app."
        )
    return token


def sync(t: Tracker) -> None:
    raise NotImplementedError


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    raise NotImplementedError
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v`
Expected: 5 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/personal_db/templates/trackers/granola/ingest.py tests/unit/test_granola_tracker.py tests/unit/fixtures/granola/
git commit -m "feat(granola): read access token from desktop supabase.json"
```

---

## Task 3: `_prosemirror_to_text` — best-effort plaintext extraction

**Files:**
- Modify: `src/personal_db/templates/trackers/granola/ingest.py`
- Modify: `tests/unit/test_granola_tracker.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_granola_tracker.py`:

```python
def test_prosemirror_to_text_single_paragraph():
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [
            {"type": "text", "text": "hello world"}
        ]}
    ]}
    assert granola_ingest._prosemirror_to_text(doc) == "hello world"


def test_prosemirror_to_text_two_paragraphs():
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "first"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "second"}]},
    ]}
    assert granola_ingest._prosemirror_to_text(doc) == "first\nsecond"


def test_prosemirror_to_text_heading_and_body():
    doc = {"type": "doc", "content": [
        {"type": "heading", "content": [{"type": "text", "text": "Title"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "body"}]},
    ]}
    assert granola_ingest._prosemirror_to_text(doc) == "Title\nbody"


def test_prosemirror_to_text_bullet_list():
    doc = {"type": "doc", "content": [
        {"type": "bullet_list", "content": [
            {"type": "list_item", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "a"}]}
            ]},
            {"type": "list_item", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "b"}]}
            ]},
        ]},
    ]}
    out = granola_ingest._prosemirror_to_text(doc)
    assert "a" in out and "b" in out
    assert out.index("a") < out.index("b")


def test_prosemirror_to_text_none():
    assert granola_ingest._prosemirror_to_text(None) == ""


def test_prosemirror_to_text_malformed():
    # String, list at top level, dict missing "content" — none should crash
    assert granola_ingest._prosemirror_to_text("not a node") == ""
    assert granola_ingest._prosemirror_to_text([]) == ""
    assert granola_ingest._prosemirror_to_text({"type": "doc"}) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v -k prosemirror`
Expected: FAIL — `_prosemirror_to_text` does not exist.

- [ ] **Step 3: Implement `_prosemirror_to_text`**

Add to `src/personal_db/templates/trackers/granola/ingest.py` (after `_read_access_token`):

```python
_BLOCK_TYPES = {
    "paragraph", "heading", "blockquote",
    "list_item", "code_block", "bullet_list", "ordered_list",
}


def _prosemirror_to_text(node) -> str:
    """Best-effort plaintext extraction from a ProseMirror node tree.

    Recursively concatenates `text` fields. Inserts a newline after each
    block-level node so paragraphs/headings/list-items don't run together.
    Returns "" for None or malformed input rather than raising — the caller
    keeps the raw `content` JSON for fidelity.
    """
    if not isinstance(node, dict):
        return ""

    out: list[str] = []

    def walk(n) -> None:
        if not isinstance(n, dict):
            return
        if "text" in n and isinstance(n["text"], str):
            out.append(n["text"])
            return
        for child in n.get("content") or []:
            walk(child)
        if n.get("type") in _BLOCK_TYPES:
            out.append("\n")

    walk(node)
    return "".join(out).strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v -k prosemirror`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/granola/ingest.py tests/unit/test_granola_tracker.py
git commit -m "feat(granola): plaintext extraction from ProseMirror notes"
```

---

## Task 4: `_flatten` — combine doc + transcript into a row

**Files:**
- Modify: `src/personal_db/templates/trackers/granola/ingest.py`
- Modify: `tests/unit/test_granola_tracker.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_granola_tracker.py`:

```python
def _make_doc(**overrides) -> dict:
    base = {
        "id": "doc1",
        "title": "Quarterly review",
        "content": {"type": "doc", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "notes here"}]}
        ]},
        "participants": [{"name": "Alice"}, {"name": "Bob"}],
        "created_at": "2026-04-01T15:00:00Z",
        "updated_at": "2026-04-01T16:00:00Z",
    }
    base.update(overrides)
    return base


def test_flatten_with_transcript():
    doc = _make_doc()
    transcript_data = (
        "[me] hi\n[them] hello",
        "2026-04-01T15:01:00Z",
        "2026-04-01T15:31:00Z",
    )
    row = granola_ingest._flatten(doc, transcript_data)
    assert row["id"] == "doc1"
    assert row["started_at"] == "2026-04-01T15:01:00+00:00"
    assert row["finished_at"] == "2026-04-01T15:31:00+00:00"
    assert row["duration_seconds"] == 30 * 60
    assert row["title"] == "Quarterly review"
    assert row["overview"] == "notes here"
    assert json.loads(row["content"])["type"] == "doc"
    assert row["transcript"] == "[me] hi\n[them] hello"
    assert json.loads(row["participants"]) == [{"name": "Alice"}, {"name": "Bob"}]
    assert row["created_at"] == "2026-04-01T15:00:00+00:00"
    assert row["updated_at"] == "2026-04-01T16:00:00+00:00"


def test_flatten_without_transcript_falls_back_to_created_at():
    doc = _make_doc()
    row = granola_ingest._flatten(doc, ("", "", ""))
    assert row["transcript"] == ""
    assert row["started_at"] == "2026-04-01T15:00:00+00:00"  # falls back to created_at
    assert row["finished_at"] is None
    assert row["duration_seconds"] is None


def test_flatten_drops_doc_with_no_anchor():
    """No transcript and no created_at means there's no way to anchor the doc in time."""
    doc = _make_doc(created_at=None)
    assert granola_ingest._flatten(doc, ("", "", "")) is None


def test_flatten_handles_empty_content_and_participants():
    doc = _make_doc(content=None, participants=None)
    row = granola_ingest._flatten(doc, ("", "", ""))
    assert row["content"] == ""
    assert row["overview"] == ""
    assert row["participants"] == "[]"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v -k flatten`
Expected: FAIL — `_flatten` does not exist.

- [ ] **Step 3: Implement `_flatten` and helpers**

Add to `src/personal_db/templates/trackers/granola/ingest.py`:

```python
from datetime import UTC, datetime


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_utc_iso(s: str | None) -> str | None:
    dt = _parse_iso(s)
    return dt.astimezone(UTC).isoformat() if dt else None


def _duration_seconds(start: str | None, end: str | None) -> int | None:
    s, e = _parse_iso(start), _parse_iso(end)
    if not s or not e:
        return None
    return int((e - s).total_seconds())


def _flatten(doc: dict, transcript_data: tuple[str, str, str]) -> dict | None:
    """Combine a Granola doc with its fetched transcript into a row.

    Returns None if the doc has neither a transcript nor a created_at —
    we'd have nothing to anchor `started_at` to, and the schema requires it.
    """
    transcript, transcript_start, transcript_end = transcript_data
    started_raw = transcript_start or doc.get("created_at") or ""
    if not started_raw:
        return None

    finished_raw = transcript_end or None
    content_obj = doc.get("content")
    content_json = json.dumps(content_obj) if content_obj else ""
    overview = _prosemirror_to_text(content_obj) if content_obj else ""
    participants = json.dumps(doc.get("participants") or [])

    return {
        "id": doc["id"],
        "started_at": _to_utc_iso(started_raw),
        "finished_at": _to_utc_iso(finished_raw),
        "duration_seconds": _duration_seconds(started_raw, finished_raw),
        "title": (doc.get("title") or "")[:500],
        "overview": overview,
        "content": content_json,
        "transcript": transcript,
        "participants": participants,
        "created_at": _to_utc_iso(doc.get("created_at")),
        "updated_at": _to_utc_iso(doc.get("updated_at")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v -k flatten`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/granola/ingest.py tests/unit/test_granola_tracker.py
git commit -m "feat(granola): _flatten combines docs and transcripts into rows"
```

---

## Task 5: HTTP helpers — `_list_documents`, `_fetch_transcript`

**Files:**
- Modify: `src/personal_db/templates/trackers/granola/ingest.py`
- Modify: `tests/unit/test_granola_tracker.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_granola_tracker.py`:

```python
class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.HTTPError(f"{self.status_code}", response=self)
            raise err

    def json(self):
        return self._payload


def test_list_documents_array_response(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(200, [{"id": "d1", "updated_at": "2026-04-01T00:00:00Z"}])

    monkeypatch.setattr(granola_ingest.requests, "post", fake_post)
    docs = granola_ingest._list_documents("TOK", offset=0)
    assert docs == [{"id": "d1", "updated_at": "2026-04-01T00:00:00Z"}]
    assert captured["url"] == "https://api.granola.ai/v2/get-documents"
    assert captured["headers"]["Authorization"] == "Bearer TOK"
    assert captured["json"]["offset"] == 0
    assert captured["json"]["include_content"] is True


def test_list_documents_object_response(monkeypatch):
    monkeypatch.setattr(
        granola_ingest.requests, "post",
        lambda *a, **k: _FakeResponse(200, {"docs": [{"id": "d2", "updated_at": "x"}]}),
    )
    assert granola_ingest._list_documents("TOK", offset=25) == [{"id": "d2", "updated_at": "x"}]


def test_list_documents_401_raises_expired(monkeypatch):
    monkeypatch.setattr(
        granola_ingest.requests, "post",
        lambda *a, **k: _FakeResponse(401, {"error": "unauthorized"}),
    )
    with pytest.raises(RuntimeError, match="access token expired"):
        granola_ingest._list_documents("TOK", offset=0)


def test_fetch_transcript_basic(monkeypatch):
    payload = [
        {"text": "hi", "source": "me", "start_timestamp": "2026-04-01T15:00:00Z", "end_timestamp": "2026-04-01T15:00:05Z"},
        {"text": "hello", "source": "them", "start_timestamp": "2026-04-01T15:00:06Z", "end_timestamp": "2026-04-01T15:00:10Z"},
    ]
    monkeypatch.setattr(
        granola_ingest.requests, "post",
        lambda *a, **k: _FakeResponse(200, payload),
    )
    transcript, start, end = granola_ingest._fetch_transcript("TOK", "doc1")
    assert transcript == "[me] hi\n[them] hello"
    assert start == "2026-04-01T15:00:00Z"
    assert end == "2026-04-01T15:00:10Z"


def test_fetch_transcript_404_returns_empty(monkeypatch):
    monkeypatch.setattr(
        granola_ingest.requests, "post",
        lambda *a, **k: _FakeResponse(404, {"error": "no transcript"}),
    )
    assert granola_ingest._fetch_transcript("TOK", "doc1") == ("", "", "")


def test_fetch_transcript_empty_array(monkeypatch):
    monkeypatch.setattr(
        granola_ingest.requests, "post",
        lambda *a, **k: _FakeResponse(200, []),
    )
    assert granola_ingest._fetch_transcript("TOK", "doc1") == ("", "", "")


def test_fetch_transcript_skips_empty_text(monkeypatch):
    payload = [
        {"text": "hi", "source": "me", "start_timestamp": "s", "end_timestamp": "e"},
        {"text": "", "source": "them", "start_timestamp": "s2", "end_timestamp": "e2"},
    ]
    monkeypatch.setattr(
        granola_ingest.requests, "post",
        lambda *a, **k: _FakeResponse(200, payload),
    )
    transcript, _, _ = granola_ingest._fetch_transcript("TOK", "doc1")
    assert transcript == "[me] hi"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v -k "list_documents or fetch_transcript"`
Expected: FAIL — helpers do not exist.

- [ ] **Step 3: Implement `_list_documents` and `_fetch_transcript`**

Add to `src/personal_db/templates/trackers/granola/ingest.py`:

```python
import requests

LIST_URL = "https://api.granola.ai/v2/get-documents"
TRANSCRIPT_URL = "https://api.granola.ai/v1/get-document-transcript"
PAGE_SIZE = 25


def _list_documents(token: str, offset: int) -> list[dict]:
    """POST /v2/get-documents. Returns the doc array (handles both response shapes).

    Raises RuntimeError on 401 with the user-facing "expired" instruction.
    Other HTTP errors propagate via requests.HTTPError.
    """
    body = {"limit": PAGE_SIZE, "offset": offset, "include_content": True}
    r = requests.post(
        LIST_URL,
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=30,
    )
    if r.status_code == 401:
        raise RuntimeError(
            "Granola access token expired. Open the Granola desktop app to "
            "refresh, then re-run."
        )
    r.raise_for_status()
    payload = r.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("docs") or []
    return []


def _fetch_transcript(token: str, document_id: str) -> tuple[str, str, str]:
    """POST /v1/get-document-transcript. Returns (transcript, start, end).

    Returns ("", "", "") on 404, network error, or empty result. Granola has
    docs without transcripts (manual notes, missed recordings); they're stored
    with no transcript and `started_at` falls back to `created_at`.
    """
    try:
        r = requests.post(
            TRANSCRIPT_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"document_id": document_id},
            timeout=30,
        )
    except requests.RequestException:
        return ("", "", "")
    if r.status_code != 200:
        return ("", "", "")
    try:
        utterances = r.json()
    except ValueError:
        return ("", "", "")
    if not isinstance(utterances, list) or not utterances:
        return ("", "", "")

    lines = []
    for u in utterances:
        text = (u.get("text") or "").strip()
        if not text:
            continue
        source = u.get("source") or "unknown"
        lines.append(f"[{source}] {text}")
    transcript = "\n".join(lines)
    start = utterances[0].get("start_timestamp") or ""
    end = utterances[-1].get("end_timestamp") or ""
    return (transcript, start, end)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v -k "list_documents or fetch_transcript"`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/granola/ingest.py tests/unit/test_granola_tracker.py
git commit -m "feat(granola): list/transcript HTTP helpers with explicit 401 handling"
```

---

## Task 6: `sync` — paged cursor walk

**Files:**
- Modify: `src/personal_db/templates/trackers/granola/ingest.py`
- Modify: `tests/unit/test_granola_tracker.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_granola_tracker.py`:

```python
@pytest.fixture
def fake_tracker(tmp_path, monkeypatch):
    """Build a Tracker pointing at a temp DB + state dir."""
    from personal_db.config import Config
    from personal_db.tracker import Tracker

    cfg = Config(root=tmp_path)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)

    import sqlite3
    schema = (Path(__file__).resolve().parents[2]
              / "src/personal_db/templates/trackers/granola/schema.sql").read_text()
    con = sqlite3.connect(cfg.db_path)
    con.executescript(schema)
    con.commit()
    con.close()

    return Tracker(name="granola", cfg=cfg, manifest=None)


def _install_fake_http(monkeypatch, pages, transcript=("", "", "")):
    """Make _list_documents return successive `pages`, _fetch_transcript return `transcript`."""
    calls = {"list_offsets": [], "transcript_ids": []}
    pages_iter = iter(pages)

    def fake_list(token, offset):
        calls["list_offsets"].append(offset)
        try:
            return next(pages_iter)
        except StopIteration:
            return []

    def fake_transcript(token, doc_id):
        calls["transcript_ids"].append(doc_id)
        return transcript

    monkeypatch.setattr(granola_ingest, "_list_documents", fake_list)
    monkeypatch.setattr(granola_ingest, "_fetch_transcript", fake_transcript)
    monkeypatch.setattr(granola_ingest, "_read_access_token", lambda: "TOK")
    return calls


def test_sync_empty_store_fetches_all_pages(fake_tracker, monkeypatch):
    page1 = [
        {"id": "d1", "title": "t1", "content": None, "participants": [],
         "created_at": "2026-04-10T10:00:00Z", "updated_at": "2026-04-10T10:00:00Z"},
        {"id": "d2", "title": "t2", "content": None, "participants": [],
         "created_at": "2026-04-09T10:00:00Z", "updated_at": "2026-04-09T10:00:00Z"},
    ]
    calls = _install_fake_http(monkeypatch, [page1])
    granola_ingest.sync(fake_tracker)

    import sqlite3
    con = sqlite3.connect(fake_tracker.cfg.db_path)
    rows = con.execute("SELECT id FROM granola_documents ORDER BY id").fetchall()
    con.close()
    assert [r[0] for r in rows] == ["d1", "d2"]
    assert fake_tracker.cursor.get() == "2026-04-10T10:00:00+00:00"
    assert calls["transcript_ids"] == ["d1", "d2"]


def test_sync_skips_older_docs_within_partial_page(fake_tracker, monkeypatch):
    """A partial page (< PAGE_SIZE) terminates the loop, but per-doc cursor filtering still applies."""
    fake_tracker.cursor.set("2026-04-09T12:00:00+00:00")
    page1 = [
        {"id": "d_new", "title": "new", "content": None, "participants": [],
         "created_at": "2026-04-10T10:00:00Z", "updated_at": "2026-04-10T10:00:00Z"},
        {"id": "d_old", "title": "old", "content": None, "participants": [],
         "created_at": "2026-04-08T10:00:00Z", "updated_at": "2026-04-08T10:00:00Z"},
    ]
    calls = _install_fake_http(monkeypatch, [page1])
    granola_ingest.sync(fake_tracker)

    # Only d_new is past the cursor; d_old is skipped.
    # Loop exits via `len(docs) < PAGE_SIZE` after this single page.
    assert calls["transcript_ids"] == ["d_new"]
    assert calls["list_offsets"] == [0]


def test_sync_breaks_when_full_page_is_older_than_cursor(fake_tracker, monkeypatch):
    """When page 1 is full of new docs (so the loop advances) but page 2 is fully older,
    we fetch page 2 once to confirm it's older, then stop without fetching page 3."""
    fake_tracker.cursor.set("2026-04-09T12:00:00+00:00")
    # 25 new docs — all past the cursor; this fills the page so the loop advances.
    page1 = [
        {"id": f"new{i:02d}", "title": "n", "content": None, "participants": [],
         "created_at": f"2026-04-{15 + i:02d}T10:00:00Z",
         "updated_at": f"2026-04-{15 + i:02d}T10:00:00Z"}
        for i in range(25)
    ]
    # Page 2 is fully older — triggers the cursor-based break.
    page2_all_older = [
        {"id": "d_old", "title": "old", "content": None, "participants": [],
         "created_at": "2026-04-05T10:00:00Z", "updated_at": "2026-04-05T10:00:00Z"},
    ]
    page3_should_not_be_fetched = [
        {"id": "d_x", "title": "x", "content": None, "participants": [],
         "created_at": "2026-04-01T10:00:00Z", "updated_at": "2026-04-01T10:00:00Z"},
    ]
    calls = _install_fake_http(monkeypatch, [page1, page2_all_older, page3_should_not_be_fetched])
    granola_ingest.sync(fake_tracker)

    # All 25 page-1 docs are new → 25 transcript fetches.
    # Page 2 has no new docs → 0 additional transcript fetches.
    # Page 3 must not be fetched at all.
    assert len(calls["transcript_ids"]) == 25
    assert calls["list_offsets"] == [0, 25]


def test_sync_no_results_no_cursor_change(fake_tracker, monkeypatch):
    fake_tracker.cursor.set("2026-04-09T12:00:00+00:00")
    calls = _install_fake_http(monkeypatch, [[]])
    granola_ingest.sync(fake_tracker)
    assert fake_tracker.cursor.get() == "2026-04-09T12:00:00+00:00"
    assert calls["transcript_ids"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v -k sync`
Expected: FAIL — `sync` is `NotImplementedError`.

- [ ] **Step 3: Implement `sync`**

Replace the `sync` stub in `src/personal_db/templates/trackers/granola/ingest.py`:

```python
def sync(t: Tracker) -> None:
    """Pull new/edited Granola docs since the cursor.

    Cursor: max(updated_at) of stored docs. Pages newest-first; stops when a
    full page is older than the cursor.
    """
    token = _read_access_token()
    cursor = t.cursor.get()
    fetched: list[dict] = []
    page = 0
    while True:
        docs = _list_documents(token, offset=page * PAGE_SIZE)
        if not docs:
            break
        page_max_updated = max(d.get("updated_at") or "" for d in docs)
        for doc in docs:
            if cursor and (doc.get("updated_at") or "") <= cursor:
                continue
            transcript_data = _fetch_transcript(token, doc["id"])
            row = _flatten(doc, transcript_data)
            if row is not None:
                fetched.append(row)
        if cursor and page_max_updated <= cursor:
            break
        if len(docs) < PAGE_SIZE:
            break
        page += 1

    if fetched:
        t.upsert("granola_documents", fetched, key=["id"])
        t.cursor.set(max(r["updated_at"] for r in fetched))
    t.log.info("granola: ingested %d documents", len(fetched))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v -k sync`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/granola/ingest.py tests/unit/test_granola_tracker.py
git commit -m "feat(granola): paged sync with updated_at cursor"
```

---

## Task 7: `backfill` — full-history sweep with optional start window

**Files:**
- Modify: `src/personal_db/templates/trackers/granola/ingest.py`
- Modify: `tests/unit/test_granola_tracker.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_granola_tracker.py`:

```python
def test_backfill_walks_all_pages_when_start_none(fake_tracker, monkeypatch):
    page1 = [
        {"id": "d1", "title": "t1", "content": None, "participants": [],
         "created_at": "2026-04-10T10:00:00Z", "updated_at": "2026-04-10T10:00:00Z"},
    ]
    page2 = [
        {"id": "d2", "title": "t2", "content": None, "participants": [],
         "created_at": "2026-03-10T10:00:00Z", "updated_at": "2026-03-10T10:00:00Z"},
    ]
    calls = _install_fake_http(monkeypatch, [page1 + [{"id": f"f{i}", "title": "filler",
                                                       "content": None, "participants": [],
                                                       "created_at": f"2026-04-{i+1:02d}T00:00:00Z",
                                                       "updated_at": f"2026-04-{i+1:02d}T00:00:00Z"}
                                                       for i in range(24)],
                                              page2])
    granola_ingest.backfill(fake_tracker, start=None, end=None)
    # Both pages walked (page1 was full, so we advance)
    assert calls["list_offsets"] == [0, 25]
    assert "d1" in calls["transcript_ids"] and "d2" in calls["transcript_ids"]


def test_backfill_stops_at_start_window(fake_tracker, monkeypatch):
    page1 = [
        {"id": "d_new", "title": "new", "content": None, "participants": [],
         "created_at": "2026-04-10T10:00:00Z", "updated_at": "2026-04-10T10:00:00Z"},
        {"id": "d_old", "title": "old", "content": None, "participants": [],
         "created_at": "2026-01-05T10:00:00Z", "updated_at": "2026-01-05T10:00:00Z"},
    ]
    calls = _install_fake_http(monkeypatch, [page1])
    granola_ingest.backfill(fake_tracker, start="2026-02-01", end=None)
    # d_old.created_at < start → loop returns; only d_new gets a transcript fetch
    assert calls["transcript_ids"] == ["d_new"]


def test_backfill_ignores_end(fake_tracker, monkeypatch):
    """end is accepted for interface compatibility but ignored, like omi's backfill."""
    calls = _install_fake_http(monkeypatch, [[]])
    granola_ingest.backfill(fake_tracker, start=None, end="2026-04-01")
    # No crash, no calls beyond an empty first page
    assert calls["list_offsets"] == [0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v -k backfill`
Expected: FAIL — `backfill` is `NotImplementedError`.

- [ ] **Step 3: Implement `backfill`**

Replace the `backfill` stub in `src/personal_db/templates/trackers/granola/ingest.py`:

```python
def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    """Backfill Granola docs.

    `start` (ISO date or datetime) bounds the walk by `created_at`. `end` is
    accepted for interface compatibility but ignored — the API has no upper
    bound and we always page from newest.
    """
    del end
    token = _read_access_token()
    fetched: list[dict] = []
    page = 0
    stop = False
    while not stop:
        docs = _list_documents(token, offset=page * PAGE_SIZE)
        if not docs:
            break
        for doc in docs:
            if start and (doc.get("created_at") or "") < start:
                stop = True
                break
            transcript_data = _fetch_transcript(token, doc["id"])
            row = _flatten(doc, transcript_data)
            if row is not None:
                fetched.append(row)
        if len(docs) < PAGE_SIZE:
            break
        page += 1

    if fetched:
        t.upsert("granola_documents", fetched, key=["id"])
        t.cursor.set(max(r["updated_at"] for r in fetched))
    t.log.info("granola: backfilled %d documents", len(fetched))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v`
Expected: ALL tests PASS (the full file).

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/granola/ingest.py tests/unit/test_granola_tracker.py
git commit -m "feat(granola): backfill with optional created_at start window"
```

---

## Task 8: Visualizations — port from omi

**Files:**
- Modify: `src/personal_db/templates/trackers/granola/visualizations.py`
- Modify: `tests/unit/test_granola_tracker.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_granola_tracker.py`:

```python
def test_visualizations_listed():
    from personal_db.templates.trackers.granola import visualizations as viz
    out = viz.list_visualizations()
    slugs = [v["slug"] for v in out]
    assert "activity_calendar" in slugs
    assert "recent" in slugs
    for v in out:
        assert callable(v["render"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py::test_visualizations_listed -v`
Expected: FAIL — list returns `[]`.

- [ ] **Step 3: Implement visualizations**

Replace `src/personal_db/templates/trackers/granola/visualizations.py`:

```python
"""Visualizations for the granola tracker (meeting notes + transcripts)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from html import escape

from personal_db.config import Config
from personal_db.ui.charts import calendar_grid


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_activity_calendar(cfg: Config) -> str:
    """13-week grid: cell darkness = number of meetings started that day."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = date.today()
    weeks = 13
    start = today - timedelta(days=weeks * 7 - 1)
    try:
        rows = con.execute(
            "SELECT date(started_at, 'localtime') AS d, count(*) AS n "
            "FROM granola_documents WHERE started_at >= ? GROUP BY d",
            (start.isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">granola not synced yet</p>'
    finally:
        con.close()

    by_day: dict[date, float] = {}
    for d_str, n in rows:
        try:
            by_day[date.fromisoformat(d_str)] = float(n)
        except (TypeError, ValueError):
            continue
    if not by_day:
        return f'<p class="meta">no Granola meetings in the last {weeks} weeks</p>'
    total = int(sum(by_day.values()))
    return (
        f'<p class="meta">{total} meetings in the last {weeks} weeks · '
        "darker cells = more meetings</p>"
        + calendar_grid(by_day, end_date=today, weeks=weeks)
    )


def render_recent(cfg: Config) -> str:
    """Last 20 meetings with title, time, duration, and overview snippet."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    try:
        rows = con.execute(
            "SELECT started_at, title, overview, duration_seconds "
            "FROM granola_documents ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">granola not synced yet</p>'
    finally:
        con.close()
    if not rows:
        return '<p class="meta">no Granola meetings yet</p>'

    items = []
    for started_at, title, overview, duration in rows:
        try:
            started = datetime.fromisoformat(started_at).astimezone()
            when = started.strftime("%b %d %H:%M")
        except (TypeError, ValueError):
            when = started_at or "?"
        dur_min = round((duration or 0) / 60)
        meta = f"{when} · {dur_min}m"
        snippet = (overview or "").strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:237] + "…"
        items.append(
            f'<li><strong>{escape(title or "(untitled)")}</strong>'
            f'<span class="meta"> — {meta}</span>'
            f'{"<br>" + escape(snippet) if snippet else ""}</li>'
        )
    return f'<ul class="granola-recent">{"".join(items)}</ul>'


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "activity_calendar",
            "name": "Meeting Calendar (13w)",
            "description": "13-week grid colored by daily meeting count.",
            "render": render_activity_calendar,
        },
        {
            "slug": "recent",
            "name": "Recent Meetings",
            "description": "20 most recent Granola meetings with title, time, and overview.",
            "render": render_recent,
        },
    ]
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `.venv/bin/python -m pytest tests/unit/test_granola_tracker.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/granola/visualizations.py tests/unit/test_granola_tracker.py
git commit -m "feat(granola): activity calendar + recent meetings visualizations"
```

---

## Task 9: Install on the live root and smoke test

This is a manual smoke step — verifies the bundled tracker installs cleanly into the user's actual `~/personal_db` and that one sync run reaches Granola's API. There are no new code changes here.

**Files:** none (verification only).

- [ ] **Step 1: Reinstall bundled templates into the live root**

```bash
.venv/bin/personal-db --root ~/personal_db tracker install granola 2>&1 || \
.venv/bin/personal-db --root ~/personal_db tracker reinstall granola
```

The `tracker install` succeeds on a fresh root; if granola is already installed (e.g. from a previous test run), `reinstall` is required. One of the two will succeed.

Expected: command exits 0; `~/personal_db/trackers/granola/` contains the four canonical files.

- [ ] **Step 2: Confirm the table exists**

```bash
sqlite3 ~/personal_db/db.sqlite ".schema granola_documents"
```

Expected: prints the `CREATE TABLE granola_documents (...)` DDL.

- [ ] **Step 3: Run a single sync**

```bash
.venv/bin/personal-db --root ~/personal_db sync granola
```

Three possible outcomes:
- **Success** — log line `granola: ingested N documents`, with `N >= 0`. ✅
- **`Granola desktop app not detected`** — Granola isn't installed on this machine. Acceptable if developing without the app; record in commit message and skip the live smoke. ⚠️
- **`Granola access token expired. Open the Granola desktop app to refresh`** — open Granola desktop, then re-run. Verifies the error path works. ✅

- [ ] **Step 4: If sync succeeded, spot-check a row**

```bash
sqlite3 ~/personal_db/db.sqlite \
  "SELECT id, started_at, title, length(transcript), length(content) FROM granola_documents ORDER BY started_at DESC LIMIT 5"
```

Expected: 0 to 5 rows. For each row: `started_at` is a valid ISO-8601 UTC timestamp, `title` is non-empty for real meetings, `transcript` is empty or speaker-prefixed text, `content` is empty or a JSON blob.

- [ ] **Step 5: Run the full unit test suite**

```bash
.venv/bin/python -m pytest tests/unit/ -q
```

Expected: full pass. No regressions in other trackers.

- [ ] **Step 6: Commit any final tweaks**

If the smoke run surfaced anything unexpected (e.g. Granola's API response shape differs from what the tests assume), update the helpers + tests, then:

```bash
git add -u
git commit -m "fix(granola): <describe what was wrong>"
```

If everything passed cleanly, no commit needed for this task.

---

## Notes for the implementer

- **Stay TDD.** Each task writes the failing test first, runs it to confirm it fails, then implements. Do not skip the "run and confirm failure" step — it catches typos in the test that would otherwise create a false-pass.
- **Don't refresh tokens.** The whole design is built on "we don't refresh." If you find yourself writing a `_refresh_token` helper, stop — re-read the spec. The Granola desktop app handles refresh; we re-read its token on every sync.
- **`time_column: started_at`, cursor on `updated_at`.** These are different columns by design. Don't unify them.
- **`_flatten` returns `None`** for docs without an anchor — both sync and backfill must skip `None` returns.
- **API shape uncertainty.** The Granola list endpoint may return `[...]` or `{"docs": [...]}`. We handle both. If you see a third shape in the wild during the smoke step, add a case + test.
