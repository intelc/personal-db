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


def test_read_access_token_corrupt_json(tmp_path):
    p = tmp_path / "supabase.json"
    p.write_text("{ this is not json")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        granola_ingest._read_access_token(p)


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
    # Pin exact output: each block-level node closes with "\n"; outer .strip()
    # only trims edges. Locks in the current separator behavior.
    assert granola_ingest._prosemirror_to_text(doc) == "a\n\nb"


def test_prosemirror_to_text_non_list_content_silently_dropped():
    """A node where `content` is a string/dict/etc. (malformed) yields "" with no crash."""
    assert granola_ingest._prosemirror_to_text(
        {"type": "paragraph", "content": "literal text"}
    ) == ""
    assert granola_ingest._prosemirror_to_text(
        {"type": "doc", "content": {"nested": "dict, not a list"}}
    ) == ""


def test_prosemirror_to_text_blockquote():
    doc = {"type": "doc", "content": [
        {"type": "blockquote", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "quoted"}]}
        ]},
        {"type": "paragraph", "content": [{"type": "text", "text": "after"}]},
    ]}
    out = granola_ingest._prosemirror_to_text(doc)
    assert "quoted" in out
    assert "after" in out
    assert out.index("quoted") < out.index("after")


def test_prosemirror_to_text_none():
    assert granola_ingest._prosemirror_to_text(None) == ""


def test_prosemirror_to_text_malformed():
    # String, list at top level, dict missing "content" — none should crash
    assert granola_ingest._prosemirror_to_text("not a node") == ""
    assert granola_ingest._prosemirror_to_text([]) == ""
    assert granola_ingest._prosemirror_to_text({"type": "doc"}) == ""


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


def test_flatten_drops_doc_with_missing_id():
    """No id means we can't dedupe — and the schema PK forbids it anyway."""
    doc = _make_doc()
    doc.pop("id")
    assert granola_ingest._flatten(doc, ("[me] hi", "2026-04-01T15:00:00Z", "2026-04-01T15:30:00Z")) is None


def test_flatten_drops_doc_with_unparseable_started_at():
    """Non-empty but malformed timestamps don't sneak past the anchor guard."""
    doc = _make_doc(created_at="not a real timestamp")
    assert granola_ingest._flatten(doc, ("", "", "")) is None


def test_duration_seconds_clamps_negative_to_zero():
    """End before start (clock skew, DST) must not produce negative durations."""
    assert granola_ingest._duration_seconds("2026-04-01T15:30:00Z", "2026-04-01T15:00:00Z") == 0


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
    assert captured["json"]["limit"] == 25  # PAGE_SIZE


def test_list_documents_object_response(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, {"docs": [{"id": "d2", "updated_at": "x"}]})

    monkeypatch.setattr(granola_ingest.requests, "post", fake_post)
    assert granola_ingest._list_documents("TOK", offset=25) == [{"id": "d2", "updated_at": "x"}]
    assert captured["json"]["offset"] == 25


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
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, payload)

    monkeypatch.setattr(granola_ingest.requests, "post", fake_post)
    transcript, start, end = granola_ingest._fetch_transcript("TOK", "doc1")
    assert transcript == "[me] hi\n[them] hello"
    assert start == "2026-04-01T15:00:00Z"
    assert end == "2026-04-01T15:00:10Z"
    assert captured["json"]["document_id"] == "doc1"


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


def test_fetch_transcript_network_error_returns_empty(monkeypatch):
    """A connection error / timeout / etc. is swallowed, not propagated."""
    import requests as _requests

    def boom(*a, **k):
        raise _requests.ConnectionError("network down")

    monkeypatch.setattr(granola_ingest.requests, "post", boom)
    assert granola_ingest._fetch_transcript("TOK", "doc1") == ("", "", "")


def test_fetch_transcript_401_raises_expired(monkeypatch):
    """A 401 on the transcript endpoint surfaces just like the list endpoint."""
    monkeypatch.setattr(
        granola_ingest.requests, "post",
        lambda *a, **k: _FakeResponse(401, {"error": "unauthorized"}),
    )
    with pytest.raises(RuntimeError, match="access token expired"):
        granola_ingest._fetch_transcript("TOK", "doc1")


def test_fetch_transcript_whitespace_only_text_preserves_timestamps(monkeypatch):
    """Utterances with strip()-empty text leave transcript empty but keep timestamps."""
    payload = [
        {"text": "   ", "source": "me",
         "start_timestamp": "2026-01-01T10:00:00Z",
         "end_timestamp": "2026-01-01T10:00:05Z"},
    ]
    monkeypatch.setattr(
        granola_ingest.requests, "post",
        lambda *a, **k: _FakeResponse(200, payload),
    )
    transcript, start, end = granola_ingest._fetch_transcript("TOK", "doc1")
    assert transcript == ""
    assert start == "2026-01-01T10:00:00Z"
    assert end == "2026-01-01T10:00:05Z"


@pytest.fixture
def fake_tracker(tmp_path, monkeypatch):
    """Build a Tracker pointing at a temp DB + state dir."""
    from personal_db.core.config import Config
    from personal_db.core.tracker import Tracker

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
    # Use a FULL 25-doc page so only the cursor break can stop the loop —
    # otherwise the partial-page break would mask cursor-break regressions.
    page2_all_older = [
        {"id": f"old{i:02d}", "title": "old", "content": None, "participants": [],
         "created_at": "2026-04-05T10:00:00Z", "updated_at": "2026-04-05T10:00:00Z"}
        for i in range(25)
    ]
    page3_should_not_be_fetched = [
        {"id": "d_x", "title": "x", "content": None, "participants": [],
         "created_at": "2026-04-01T10:00:00Z", "updated_at": "2026-04-01T10:00:00Z"},
    ]
    calls = _install_fake_http(monkeypatch, [page1, page2_all_older, page3_should_not_be_fetched])
    granola_ingest.sync(fake_tracker)

    # All 25 page-1 docs are new → 25 transcript fetches.
    # Page 2 is a full page of docs all older than the cursor →
    # cursor break fires, 0 additional transcript fetches, page 3 never requested.
    assert len(calls["transcript_ids"]) == 25
    assert calls["list_offsets"] == [0, 25]


def test_sync_no_results_no_cursor_change(fake_tracker, monkeypatch):
    fake_tracker.cursor.set("2026-04-09T12:00:00+00:00")
    calls = _install_fake_http(monkeypatch, [[]])
    granola_ingest.sync(fake_tracker)
    assert fake_tracker.cursor.get() == "2026-04-09T12:00:00+00:00"
    assert calls["transcript_ids"] == []


def test_sync_normalizes_z_suffix_in_cursor_comparison(fake_tracker, monkeypatch):
    """API returns Z-suffixed timestamps; cursor uses +00:00. Same instant
    must compare equal so docs at the cursor boundary aren't re-fetched."""
    # Cursor matches the doc's updated_at instant (in +00:00 form).
    fake_tracker.cursor.set("2026-04-10T10:00:00+00:00")
    page1 = [
        {"id": "d_at_cursor", "title": "boundary", "content": None, "participants": [],
         "created_at": "2026-04-10T10:00:00Z", "updated_at": "2026-04-10T10:00:00Z"},
    ]
    calls = _install_fake_http(monkeypatch, [page1])
    granola_ingest.sync(fake_tracker)
    # Doc is exactly at the cursor → must be skipped, no transcript fetch.
    assert calls["transcript_ids"] == []


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


def test_backfill_normalizes_z_suffix_in_start_comparison(fake_tracker, monkeypatch):
    """A +00:00 start value should match Z-form API timestamps at the boundary."""
    page1 = [
        {"id": "d_at_boundary", "title": "boundary", "content": None, "participants": [],
         "created_at": "2026-02-01T00:00:00Z", "updated_at": "2026-02-01T00:00:00Z"},
    ]
    calls = _install_fake_http(monkeypatch, [page1])
    granola_ingest.backfill(fake_tracker, start="2026-02-01T00:00:00+00:00", end=None)
    # Doc.created_at == start → NOT older than start → fetched.
    assert calls["transcript_ids"] == ["d_at_boundary"]


def test_backfill_does_not_regress_cursor_below_sync_value(fake_tracker, monkeypatch):
    """Backfill of old data must not pull cursor back below where sync left it."""
    # Sync had already advanced the cursor to a recent timestamp.
    fake_tracker.cursor.set("2026-04-15T00:00:00+00:00")
    # Backfill returns an OLDER doc (max updated_at < cursor).
    page1 = [
        {"id": "old_doc", "title": "old", "content": None, "participants": [],
         "created_at": "2026-01-10T00:00:00Z", "updated_at": "2026-01-10T00:00:00Z"},
    ]
    _install_fake_http(monkeypatch, [page1])
    granola_ingest.backfill(fake_tracker, start=None, end=None)
    # Cursor must remain at the sync value, not be regressed to the old doc's updated_at.
    assert fake_tracker.cursor.get() == "2026-04-15T00:00:00+00:00"


def test_visualizations_listed():
    from personal_db.templates.trackers.granola import visualizations as viz
    out = viz.list_visualizations()
    slugs = [v["slug"] for v in out]
    assert "activity_calendar" in slugs
    assert "recent" in slugs
    for v in out:
        assert callable(v["render"])
