"""Granola tracker — pulls meeting docs and transcripts.

Auth: reads the access token directly from the Granola desktop app's local
supabase.json on every sync. We do not refresh; if the token is stale, the
user must open the Granola desktop app to refresh it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import requests

from personal_db.tracker import Tracker

SUPABASE_PATH = Path.home() / "Library/Application Support/Granola/supabase.json"

LIST_URL = "https://api.granola.ai/v2/get-documents"
TRANSCRIPT_URL = "https://api.granola.ai/v1/get-document-transcript"
PAGE_SIZE = 25


def _extract_workos_access_token(node: object) -> str | None:
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

    Raises RuntimeError with a user-facing instruction when the file is missing,
    the file contains invalid JSON, or no token can be extracted.
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


_BLOCK_TYPES = {
    "paragraph", "heading", "blockquote",
    "list_item", "code_block", "bullet_list", "ordered_list",
}


def _prosemirror_to_text(node: object) -> str:
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
        children = n.get("content")
        if isinstance(children, list):
            for child in children:
                walk(child)
        if n.get("type") in _BLOCK_TYPES:
            out.append("\n")

    walk(node)
    return "".join(out).strip()


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
    return max(0, int((e - s).total_seconds()))


def _flatten(doc: dict, transcript_data: tuple[str, str, str]) -> dict | None:
    """Combine a Granola doc with its fetched transcript into a row.

    `started_at` falls back through: transcript_start → doc.created_at.
    Returns None if the doc has no `id`, or if neither candidate parses as
    a valid ISO timestamp — the schema requires `started_at NOT NULL`.
    """
    transcript, transcript_start, transcript_end = transcript_data
    doc_id = doc.get("id")
    if not doc_id:
        return None

    started_raw = transcript_start or doc.get("created_at") or ""
    started_dt = _parse_iso(started_raw)
    if not started_dt:
        return None
    started_iso = started_dt.astimezone(UTC).isoformat()

    finished_raw = transcript_end or None
    content_obj = doc.get("content")
    content_json = json.dumps(content_obj) if content_obj else ""
    overview = _prosemirror_to_text(content_obj) if content_obj else ""
    participants = json.dumps(doc.get("participants") or [])

    return {
        "id": doc_id,
        "started_at": started_iso,
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

    A 401 is NOT swallowed — it's the same expired-token signal as the list
    endpoint, surfaced as a RuntimeError so the user knows to re-auth.
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
    if r.status_code == 401:
        raise RuntimeError(
            "Granola access token expired. Open the Granola desktop app to "
            "refresh, then re-run."
        )
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
    # Timestamps come from raw first/last utterance bounds, not the filtered
    # lines — so started_at reflects when recording began even if the first
    # segment was silence/empty.
    start = utterances[0].get("start_timestamp") or ""
    end = utterances[-1].get("end_timestamp") or ""
    return (transcript, start, end)


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
        # Granola's API returns Z-suffixed ISO strings; the cursor (from
        # _to_utc_iso) is +00:00-suffixed. Normalize before string-comparing
        # so the same instant compares equal in both forms.
        page_max_updated = max(
            (d.get("updated_at") or "").replace("Z", "+00:00") for d in docs
        )
        for doc in docs:
            doc_updated = (doc.get("updated_at") or "").replace("Z", "+00:00")
            if cursor and doc_updated <= cursor:
                continue
            transcript_data = _fetch_transcript(token, doc["id"])
            row = _flatten(doc, transcript_data)
            if row is not None:
                fetched.append(row)
            # Note: if _flatten returns None (no id, or unparseable started_at),
            # the doc is omitted from `fetched` and the cursor doesn't advance
            # past it. It will be re-fetched on every sync. This is intentional —
            # silently advancing past a malformed doc could cause data loss if
            # the missing field is transient.
        if cursor and page_max_updated <= cursor:
            break
        if len(docs) < PAGE_SIZE:
            break
        page += 1

    new_cursor = max(r["updated_at"] for r in fetched) if fetched else cursor
    if fetched:
        t.upsert("granola_documents", fetched, key=["id"])
        t.cursor.set(new_cursor)
    t.log.info("granola: ingested %d documents (cursor → %s)", len(fetched), new_cursor)


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
    # Normalize start the same way we normalize updated_at in sync, so a
    # user-supplied "+00:00" start matches the API's "Z"-suffixed created_at.
    start_normalized = start.replace("Z", "+00:00") if start else None
    while not stop:
        docs = _list_documents(token, offset=page * PAGE_SIZE)
        if not docs:
            break
        for doc in docs:
            doc_created = (doc.get("created_at") or "").replace("Z", "+00:00")
            if start_normalized and doc_created < start_normalized:
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
        # Cursor only advances — never regresses. Backfill called after sync
        # has already advanced the cursor must not pull it back below where
        # sync left off, or the next sync would re-fetch already-known docs.
        max_backfilled = max(r["updated_at"] for r in fetched)
        existing_cursor = t.cursor.get() or ""
        t.cursor.set(max(existing_cursor, max_backfilled))
    new_cursor = t.cursor.get()
    t.log.info(
        "granola: backfilled %d documents (start=%s, cursor → %s)",
        len(fetched),
        start or "all-time",
        new_cursor or "(unset)",
    )
