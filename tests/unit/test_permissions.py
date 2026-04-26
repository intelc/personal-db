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
