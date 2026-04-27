"""Tests for personal_db.app_names - the 5-tier bundle-ID resolver."""

from __future__ import annotations

import json

import personal_db.app_names as app_names
from personal_db.app_names import resolve_app_name

# ---------------------------------------------------------------------------
# Tier 1: hardcoded overrides
# ---------------------------------------------------------------------------


def test_resolve_returns_hardcoded_override():
    assert resolve_app_name("com.apple.mobilesafari") == "Safari"
    assert resolve_app_name("com.apple.springboard") == "Home Screen"
    assert resolve_app_name("com.apple.systempreferences") == "System Settings"


# ---------------------------------------------------------------------------
# Tier 2: mdfind / Info.plist
# ---------------------------------------------------------------------------


def test_resolve_uses_local_app_when_mdfind_finds_one(monkeypatch, tmp_path):
    """When _name_from_local_app returns a value it should be used immediately."""
    monkeypatch.setattr(app_names, "_name_from_local_app", lambda _bid: "Cursor")
    monkeypatch.setattr(
        app_names,
        "_name_from_itunes",
        lambda _bid: (_ for _ in ()).throw(AssertionError("iTunes should not be called")),
    )

    result = resolve_app_name("com.todesktop.230313mzl4w4u92", cache_path=tmp_path / "cache.json")
    assert result == "Cursor"


# ---------------------------------------------------------------------------
# Tier 3: JSON cache
# ---------------------------------------------------------------------------


def test_resolve_uses_cache_when_set(monkeypatch, tmp_path):
    """Pre-seeded cache should short-circuit mdfind and iTunes."""
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({"com.example.MyApp": "My App"}))

    called = {"mdfind": False, "itunes": False}

    def fake_mdfind(bid):
        called["mdfind"] = True
        return None

    def fake_itunes(bid):
        called["itunes"] = True
        return None

    monkeypatch.setattr(app_names, "_name_from_local_app", fake_mdfind)
    monkeypatch.setattr(app_names, "_name_from_itunes", fake_itunes)

    result = resolve_app_name("com.example.MyApp", cache_path=cache_file)
    assert result == "My App"
    assert not called["mdfind"]
    assert not called["itunes"]


# ---------------------------------------------------------------------------
# Tier 4: iTunes API
# ---------------------------------------------------------------------------


def test_resolve_falls_back_to_itunes(monkeypatch, tmp_path):
    """When mdfind returns None, iTunes result should be used and cached."""
    cache_file = tmp_path / "cache.json"

    monkeypatch.setattr(app_names, "_name_from_local_app", lambda _: None)
    monkeypatch.setattr(app_names, "_name_from_itunes", lambda _: "Some App")

    result = resolve_app_name("com.example.SomeApp", cache_path=cache_file)
    assert result == "Some App"

    # Result must be persisted to the cache file
    cached = json.loads(cache_file.read_text())
    assert cached.get("com.example.SomeApp") == "Some App"


# ---------------------------------------------------------------------------
# Negative cache
# ---------------------------------------------------------------------------


def test_resolve_caches_negative_result(monkeypatch, tmp_path):
    """When all tiers fail, None is cached so iTunes isn't re-queried next call."""
    cache_file = tmp_path / "cache.json"

    itunes_call_count = {"n": 0}

    def fake_itunes(bid):
        itunes_call_count["n"] += 1
        return None

    monkeypatch.setattr(app_names, "_name_from_local_app", lambda _: None)
    monkeypatch.setattr(app_names, "_name_from_itunes", fake_itunes)

    # First call: all tiers miss
    r1 = resolve_app_name("com.unknown.gibberish", cache_path=cache_file)
    assert itunes_call_count["n"] == 1

    # Second call: cache hit (null), iTunes must NOT be called again
    r2 = resolve_app_name("com.unknown.gibberish", cache_path=cache_file)
    assert itunes_call_count["n"] == 1  # unchanged
    # Both calls should return the same fallback
    assert r1 == r2


# ---------------------------------------------------------------------------
# Tier 5: CamelCase fallback
# ---------------------------------------------------------------------------


def test_resolve_camelcase_fallback(monkeypatch, tmp_path):
    """CamelCase bundle ID segment should be split into words."""
    monkeypatch.setattr(app_names, "_name_from_local_app", lambda _: None)
    monkeypatch.setattr(app_names, "_name_from_itunes", lambda _: None)

    result = resolve_app_name(
        "com.example.MyCoolApp",
        cache_path=tmp_path / "cache.json",
    )
    assert result == "My Cool App"
