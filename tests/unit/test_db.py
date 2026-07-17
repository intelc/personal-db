import sqlite3

import pytest

from personal_db.core.db import CORE_TABLES, connect, connection, init_db, transaction


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
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("CREATE TABLE x (a INT)")
    finally:
        con.close()


def test_connection_context_supports_row_factory(tmp_root):
    db_path = tmp_root / "db.sqlite"
    init_db(db_path)
    with transaction(db_path) as con:
        con.execute("CREATE TABLE x (a TEXT)")
        con.execute("INSERT INTO x(a) VALUES ('ok')")

    with connection(db_path, read_only=True, row_factory=sqlite3.Row) as con:
        row = con.execute("SELECT a FROM x").fetchone()

    assert dict(row) == {"a": "ok"}


def test_connection_context_read_only_blocks_writes(tmp_root):
    db_path = tmp_root / "db.sqlite"
    init_db(db_path)

    with connection(db_path, read_only=True) as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("CREATE TABLE x (a INT)")


def test_transaction_rolls_back_on_error(tmp_root):
    db_path = tmp_root / "db.sqlite"
    init_db(db_path)
    with transaction(db_path) as con:
        con.execute("CREATE TABLE x (a TEXT)")

    with pytest.raises(RuntimeError):
        with transaction(db_path) as con:
            con.execute("INSERT INTO x(a) VALUES ('rolled back')")
            raise RuntimeError("boom")

    with connection(db_path, read_only=True) as con:
        assert con.execute("SELECT count(*) FROM x").fetchone() == (0,)
