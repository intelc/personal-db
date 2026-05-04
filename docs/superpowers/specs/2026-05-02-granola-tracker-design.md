# Granola tracker — design

Status: approved (pending implementation plan)
Date: 2026-05-02

## Goal

Add a bundled tracker that pulls Granola meeting documents (notes + speaker-attributed transcripts) into `personal_db`. Modeled on the existing `omi` tracker, with auth borrowed from the Granola desktop app instead of a user-pasted API key.

Reference: the Swift connector at `../mosspath/Sources/mosspath/Sources/Connectors/GranolaSource.swift` documents the API contract (endpoints, response shapes) and the supabase.json layout. Our Python implementation is intentionally simpler — no token refresh.

## Non-goals

- Refreshing access tokens via WorkOS. The Granola desktop app keeps its own token fresh; we piggyback. If the app's token has expired (user hasn't opened Granola in a while), we surface a clear error and stop.
- Capturing fields beyond what the Granola list+transcript APIs return (e.g. attachments, action items if those exist as a separate endpoint).
- A "paste tokens manually" fallback. Setup is "install Granola desktop, sign in." Anything else is out of scope.

## File layout

Standard 4-file bundled tracker, no framework changes:

```
src/personal_db/templates/trackers/granola/
  __init__.py
  manifest.yaml
  schema.sql
  ingest.py
  visualizations.py
```

## Manifest

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

`permission_type: api_key` is the closest existing fit. The "key" just isn't user-pasted — it's read from the app.

## Schema

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

The `updated_at` index supports the cursor walk (see below).

## Token detection

Single helper in `ingest.py`:

```python
SUPABASE_PATH = Path.home() / "Library/Application Support/Granola/supabase.json"

def _read_access_token() -> str:
    """Read the current Granola access token from the desktop app's local store.

    Raises a clear RuntimeError when the file is missing or no token is found —
    the user needs to install / sign in to Granola desktop.
    """
```

Algorithm (mirrors `GranolaSource.detectFromApp` in mosspath, minus the JWT/client_id parsing):

1. Read `SUPABASE_PATH` → JSON.
2. Recursively walk every dict in the tree. At each node, look for a `workos_tokens` key.
3. `workos_tokens` is sometimes a JSON-encoded string, sometimes a dict. Handle both:
   - String: `json.loads(value)["access_token"]`.
   - Dict: `value["access_token"]`.
4. Return the first non-empty `access_token` found.
5. Missing file or no token → `RuntimeError("Granola desktop app not detected at {path}. Install Granola and sign in.")`.

We do **not** save the token to disk — it's re-read every sync.

## Sync algorithm

Endpoint: `POST https://api.granola.ai/v2/get-documents` with body `{"limit": 25, "offset": N, "include_content": true}` and header `Authorization: Bearer <access_token>`. Returns either a top-level array or `{"docs": [...]}` — handle both (mosspath does).

Granola has no `since` filter, so we page newest-first and stop when a page is fully older than the cursor.

```
sync(t):
  token = _read_access_token()
  cursor = t.cursor.get()                # max(updated_at) seen, or None
  page = 0
  fetched = []
  while True:
    docs = _list_documents(token, offset=page * PAGE_SIZE)
    if not docs: break
    page_max_updated = max(d["updated_at"] for d in docs)
    for doc in docs:
      if cursor and doc["updated_at"] <= cursor:
        continue
      transcript_data = _fetch_transcript(token, doc["id"])  # may be empty
      fetched.append(_flatten(doc, transcript_data))
    if cursor and page_max_updated <= cursor:
      break  # whole page is older than cursor → no new edits beyond
    if len(docs) < PAGE_SIZE:
      break  # last page
    page += 1
  if fetched:
    t.upsert("granola_documents", fetched, key=["id"])
    t.cursor.set(max(r["updated_at"] for r in fetched))
```

Note: the loop assumes the API returns docs newest-first by some recency measure. mosspath's connector also relies on this. If we ever observe out-of-order pages, soften the early-exit (continue past one out-of-window doc, only break when the whole page is older).

`fetched.append(_flatten(...))` should skip when `_flatten` returns `None` (docs without a usable `started_at`).

### Cursor

`max(updated_at)` of stored docs. Using `updated_at` (not `started_at`) because Granola edits notes after the meeting and we want to re-pull updated notes/transcripts.

### Backfill

The cursor (sync) and backfill window are intentionally asymmetric: sync re-fetches anything *edited* since last run (`updated_at` cursor), while backfill bounds by *creation time* (`created_at >= start`) — "give me meetings from this window," not "give me edits since this date."

```
backfill(t, start, end):  # end ignored, like omi
  token = _read_access_token()
  page = 0
  fetched = []
  while True:
    docs = _list_documents(token, offset=page * PAGE_SIZE)
    if not docs: break
    for doc in docs:
      if start and doc["created_at"] < start:
        return  # newest-first; everything below is older
      transcript_data = _fetch_transcript(token, doc["id"])
      fetched.append(_flatten(doc, transcript_data))
    if len(docs) < PAGE_SIZE: break
    page += 1
  if fetched:
    t.upsert("granola_documents", fetched, key=["id"])
    t.cursor.set(max(r["updated_at"] for r in fetched))
```

Default behavior (`start=None`, `end=None`) walks all-time. `start` is applied client-side since the API has no `since` parameter.

Both loops (`sync` and `backfill`) skip rows where `_flatten` returns `None`.

### Transcript fetch

Endpoint: `POST https://api.granola.ai/v1/get-document-transcript` with body `{"document_id": "..."}`.

Returns an array of utterances `[{"text": "...", "source": "me|them|...", "start_timestamp": "...", "end_timestamp": "..."}]`. We:

- Skip utterances with empty `text`.
- Format each as `"[<source>] <text>"`, joined with `\n`.
- Take `start_timestamp` of first utterance → `started_at`; `end_timestamp` of last → `finished_at`.
- 404 or any error → return `("", "", "")`. The doc still gets stored, just with no transcript and `started_at = created_at`.

### Flatten

```python
def _flatten(doc, transcript_data) -> dict:
    transcript, transcript_start, transcript_end = transcript_data
    started_at = transcript_start or doc.get("created_at") or ""
    finished_at = transcript_end or None
    duration = _duration_seconds(started_at, finished_at)

    content_obj = doc.get("content")
    content_json = json.dumps(content_obj) if content_obj else ""
    overview = _prosemirror_to_text(content_obj) if content_obj else ""

    participants = json.dumps(doc.get("participants") or [])

    if not started_at:
        return None  # drop: no transcript and no created_at, can't anchor in time

    return {
        "id": doc["id"],
        "started_at": _to_utc_iso(started_at),
        "finished_at": _to_utc_iso(finished_at),
        "duration_seconds": duration,
        "title": (doc.get("title") or "")[:500],
        "overview": overview,
        "content": content_json,
        "transcript": transcript,
        "participants": participants,
        "created_at": _to_utc_iso(doc.get("created_at")),
        "updated_at": _to_utc_iso(doc.get("updated_at")),
    }
```

`_prosemirror_to_text` recursively pulls `text` fields out of the ProseMirror tree, joining paragraphs with `\n` and trimming whitespace. Implementation note for the plan: walk `node["content"]` recursively, accumulate `node["text"]` strings, treat block-level node types (`paragraph`, `heading`, `bullet_list`, `list_item`) as paragraph breaks. Keep it small — this is a best-effort plaintext extract, not a full ProseMirror serializer.

## Error handling

| Failure | Behavior |
|---|---|
| `supabase.json` missing | `RuntimeError("Granola desktop app not detected at {path}. Install Granola and sign in.")` |
| `supabase.json` present but no `access_token` | `RuntimeError("Granola access token not found. Sign in to the Granola desktop app.")` |
| List API returns 401 | `RuntimeError("Granola access token expired. Open the Granola desktop app to refresh, then re-run.")` — no retry, no refresh. |
| List API returns 5xx / network error | propagate (sync layer logs and isolates per-tracker failures). |
| Transcript API returns 404 / network error | swallow → empty transcript; `started_at` falls back to `created_at`. |
| ProseMirror parse failure | `overview = ""`, `content` keeps raw JSON. |

## Visualizations

Direct ports of `omi`'s two views:

- **`activity_calendar`** — 13-week grid, cell darkness = number of meetings started that day, keyed on `date(started_at, 'localtime')`.
- **`recent`** — 20 most recent meetings (`ORDER BY started_at DESC LIMIT 20`), each row showing title, time, duration in minutes, and a 240-char overview snippet.

No new chart helpers needed — both reuse `personal_db.ui.charts.calendar_grid`.

## Testing

`tests/unit/test_granola_tracker.py`:

1. **Token extraction (string form)** — fixture `supabase.json` where `workos_tokens` is a JSON-encoded string nested under a project-ref key; assert `_read_access_token` returns the right access token.
2. **Token extraction (dict form)** — fixture where `workos_tokens` is already a dict; same assertion.
3. **Missing file** — `_read_access_token` raises with the install-instruction message.
4. **Empty token** — file present, `access_token == ""` → raises with the sign-in message.
5. **`_flatten` with transcript** — fixture doc + transcript; assert `started_at` = first utterance, `finished_at` = last, `duration_seconds` correct, transcript is `[me] ...\n[them] ...` formatted, `overview` extracts ProseMirror text, `content` round-trips through `json.dumps`.
6. **`_flatten` without transcript** — empty transcript response; assert `started_at` = `created_at`, transcript = `""`, `finished_at = None`.
7. **Cursor walk stops** — mock list API returns three pages; cursor set such that page 2 is fully older; assert sync stops after page 2 without fetching page 3, and only docs newer than cursor are upserted.
8. **401 on list** — mock list API returns 401; assert `RuntimeError` with the "expired" message; no retry attempt visible to the mock.

Plus the existing manifest-loads + tracker-installs smoke tests cover the manifest schema and `tracker reinstall`.

## Open implementation notes (not blockers)

- ProseMirror → plaintext is best-effort. If Granola's `content` shape turns out to be different in practice, fall back to storing raw JSON in `content` and leaving `overview = ""`.
- `PAGE_SIZE = 25`. Granola accepts higher limits (mosspath uses 10), but 25 keeps backfill snappy without risking response-size issues.
- The Swift connector hard-codes `limit: 10, offset: 0` and would miss anything beyond the latest 10 in a quiet catch-up. Our paged walk is intentionally more thorough.
