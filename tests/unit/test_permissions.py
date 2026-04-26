import sqlite3
import sys
from pathlib import Path

from personal_db.permissions import probe_sqlite_access, responsible_binary_path


def test_probe_returns_ok_for_accessible_db(tmp_path):
    p = tmp_path / "ok.sqlite"
    sqlite3.connect(p).execute("CREATE TABLE x(a)").connection.commit()
    r = probe_sqlite_access(p)
    assert r.granted is True


def test_probe_returns_denied_for_missing_path(tmp_path):
    r = probe_sqlite_access(tmp_path / "nope.sqlite")
    assert r.granted is False
    assert "missing" in r.reason.lower() or "no such" in r.reason.lower()


def test_responsible_binary_path_resolves_symlinks():
    p = responsible_binary_path()
    assert p == Path(sys.executable).resolve()
    assert p.is_absolute()


def test_probe_falls_back_to_copy_when_source_is_locked(tmp_path, monkeypatch):
    """Apps like Chrome hold an exclusive lock on their DB. The probe should copy + read."""
    p = tmp_path / "locked.sqlite"
    sqlite3.connect(p).execute("CREATE TABLE x(a)").connection.commit()

    real_connect = sqlite3.connect
    call_count = {"n": 0}

    def fake_connect(target, *a, **kw):
        call_count["n"] += 1
        # First call: direct open of source — pretend it's locked.
        # Subsequent calls (copy in tempdir): real connect.
        if call_count["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(target, *a, **kw)

    monkeypatch.setattr("personal_db.permissions.sqlite3.connect", fake_connect)
    r = probe_sqlite_access(p)
    assert r.granted is True
    assert "copy" in r.reason.lower()
