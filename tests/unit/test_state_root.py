"""Resolution precedence for personal_db.cli.state.get_root."""

from __future__ import annotations

from pathlib import Path

from personal_db.cli import state


def test_get_root_default(monkeypatch):
    """No flag, no env var → ~/personal_db."""
    monkeypatch.setattr(state, "_state", {"root": None})
    monkeypatch.delenv("PERSONAL_DB_ROOT", raising=False)
    assert state.get_root() == Path("~/personal_db").expanduser()


def test_get_root_env_var(monkeypatch, tmp_path):
    """PERSONAL_DB_ROOT wins over the default."""
    monkeypatch.setattr(state, "_state", {"root": None})
    monkeypatch.setenv("PERSONAL_DB_ROOT", str(tmp_path / "via_env"))
    assert state.get_root() == tmp_path / "via_env"


def test_get_root_env_var_expanduser(monkeypatch):
    """~ in PERSONAL_DB_ROOT is expanded."""
    monkeypatch.setattr(state, "_state", {"root": None})
    monkeypatch.setenv("PERSONAL_DB_ROOT", "~/some_other_root")
    assert state.get_root() == Path("~/some_other_root").expanduser()


def test_get_root_flag_beats_env_var(monkeypatch, tmp_path):
    """--root (sets _state['root']) wins over PERSONAL_DB_ROOT."""
    flag_path = tmp_path / "via_flag"
    monkeypatch.setattr(state, "_state", {"root": flag_path})
    monkeypatch.setenv("PERSONAL_DB_ROOT", str(tmp_path / "via_env"))
    assert state.get_root() == flag_path
