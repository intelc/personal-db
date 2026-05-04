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
