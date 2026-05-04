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
