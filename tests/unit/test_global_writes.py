from __future__ import annotations

from pathlib import Path

from personal_db.core.global_writes import blocked_reason


def test_temp_root_blocked(tmp_path):
    reason = blocked_reason(tmp_path)
    assert reason is not None
    assert "temp" in reason


def test_explicit_tmp_root_blocked():
    reason = blocked_reason(Path("/tmp/uiwork"))
    assert reason is not None


def test_real_looking_root_allowed():
    reason = blocked_reason(Path.home() / "personal_db")
    assert reason is None


def test_none_root_allowed():
    assert blocked_reason(None) is None


def test_override_env_allows_temp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONAL_DB_ALLOW_GLOBAL_WRITES", "1")
    assert blocked_reason(tmp_path) is None
