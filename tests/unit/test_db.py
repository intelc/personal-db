import sqlite3

import pytest

from personal_db.db import CORE_TABLES, connect, init_db


def test_init_db_creates_core_tables(tmp_root):
    db_path = tmp_root / "db.sqlite"
    init_db(db_path)
    con = connect(db_path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in CORE_TABLES:
        assert name in tables, f"missing core table {name}"


def test_connect_read_only_blocks_writes(tmp_root):
    db_path = tmp_root / "db.sqlite"
    init_db(db_path)
    con = connect(db_path, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("CREATE TABLE x (a INT)")
