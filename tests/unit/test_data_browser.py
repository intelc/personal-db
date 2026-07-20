"""Tests for the read-only per-tracker data browser.

Covers GET /api/v1/data/<tracker>, GET /api/v1/data/<tracker>/<table>, and
the GET /t/<tracker>/data page route (src/personal_db/services/daemon/routes/data.py
+ data_browser.html). The central guarantee under test is that `table` is
always validated against the tracker's *installed* schema.sql before it can
reach SQL -- including a table that legitimately exists in db.sqlite but
belongs to a different tracker.
"""

from __future__ import annotations

import sqlite3
import urllib.parse

import yaml
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, init_db
from personal_db.services.daemon.http import build_app
from tests._daemon_auth import auth_headers


def _install_fixture_tracker(tmp_root, name: str, schema_sql: str, table: str) -> Config:
    """Write a minimal, self-contained tracker (manifest + schema.sql + ingest.py)
    directly under <root>/trackers/<name>, matching the pattern other daemon
    route tests use (test_daemon_routes.py's `_make_runnable`) rather than
    depending on a bundled template's exact schema shape."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": f"{name} fixture",
                "permission_type": "none",
                "setup_steps": [],
                "time_column": "ts",
                "granularity": "event",
                "schema": {
                    "tables": {
                        table: {
                            "columns": {
                                "id": {"type": "INTEGER", "semantic": "id"},
                                "ts": {"type": "TEXT", "semantic": "ts"},
                            }
                        }
                    }
                },
            }
        )
    )
    (d / "schema.sql").write_text(schema_sql)
    (d / "ingest.py").write_text("def backfill(t, start, end):\n    pass\ndef sync(t):\n    pass\n")
    apply_tracker_schema(cfg.db_path, schema_sql)
    return cfg


def _insert_rows(cfg: Config, table: str, rows: list[tuple[str, str]]) -> None:
    con = sqlite3.connect(cfg.db_path)
    try:
        con.executemany(f"INSERT INTO {table} (name, ts) VALUES (?, ?)", rows)
        con.commit()
    finally:
        con.close()


def _client(cfg: Config) -> TestClient:
    return TestClient(build_app(cfg), headers=auth_headers(cfg))


_TRK_A_SCHEMA = "CREATE TABLE IF NOT EXISTS trka_items (id INTEGER PRIMARY KEY, name TEXT, ts TEXT);"
_TRK_B_SCHEMA = "CREATE TABLE IF NOT EXISTS trkb_items (id INTEGER PRIMARY KEY, name TEXT, ts TEXT);"


def test_api_data_tables_lists_tables_with_row_counts(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("a", "2026-01-01"), ("b", "2026-01-02"), ("c", "2026-01-03")])
    r = _client(cfg).get("/api/v1/data/trka")
    assert r.status_code == 200
    body = r.json()
    assert body == {"tables": [{"name": "trka_items", "row_count": 3}]}


def test_api_data_table_rows_columns_and_newest_first_order(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("first", "2026-01-01"), ("second", "2026-01-02"), ("third", "2026-01-03")])
    r = _client(cfg).get("/api/v1/data/trka/trka_items")
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["id", "name", "ts"]
    assert body["total"] == 3
    assert body["limit"] == 100
    assert body["offset"] == 0
    assert len(body["rows"]) == 3
    # ORDER BY rowid DESC -- most recently inserted row first.
    assert body["rows"][0][1] == "third"
    assert body["rows"][1][1] == "second"
    assert body["rows"][2][1] == "first"


def test_table_name_not_in_schema_is_rejected_before_reaching_sql(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("a", "2026-01-01")])

    # A name that doesn't correspond to any declared table at all.
    r = _client(cfg).get("/api/v1/data/trka/not_a_real_table")
    assert r.status_code == 404

    # A SQL-injection-shaped name -- must 404, not error out on quoting.
    evil = urllib.parse.quote('trka_items"; DROP TABLE trka_items;--', safe="")
    r = _client(cfg).get(f"/api/v1/data/trka/{evil}")
    assert r.status_code == 404

    # Data must be untouched either way.
    con = sqlite3.connect(cfg.db_path)
    try:
        (count,) = con.execute("SELECT COUNT(*) FROM trka_items").fetchone()
    finally:
        con.close()
    assert count == 1


def test_cross_tracker_table_is_rejected_even_though_it_exists_in_db(tmp_root):
    """The table exists in db.sqlite (installed by trkb, sharing the one db
    file) but isn't in trka's schema.sql -- must be rejected for trka."""
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _install_fixture_tracker(tmp_root, "trkb", _TRK_B_SCHEMA, "trkb_items")
    _insert_rows(cfg, "trkb_items", [("other", "2026-01-01")])

    r = _client(cfg).get("/api/v1/data/trka/trkb_items")
    assert r.status_code == 404

    # trkb itself can read its own table fine -- confirms the table is real,
    # just correctly scoped to its own tracker.
    r = _client(cfg).get("/api/v1/data/trkb/trkb_items")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_limit_is_clamped_not_rejected(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [(f"row{i}", "2026-01-01") for i in range(5)])

    r = _client(cfg).get("/api/v1/data/trka/trka_items?limit=0")
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 1
    assert len(body["rows"]) == 1

    r = _client(cfg).get("/api/v1/data/trka/trka_items?limit=999999")
    assert r.status_code == 200
    assert r.json()["limit"] == 500

    r = _client(cfg).get("/api/v1/data/trka/trka_items?offset=-5")
    assert r.status_code == 200
    assert r.json()["offset"] == 0


def test_without_rowid_table_falls_back_to_unordered_read(tmp_root):
    schema = (
        "CREATE TABLE IF NOT EXISTS trka_items "
        "(id TEXT PRIMARY KEY, name TEXT, ts TEXT) WITHOUT ROWID;"
    )
    cfg = _install_fixture_tracker(tmp_root, "trka", schema, "trka_items")
    con = sqlite3.connect(cfg.db_path)
    try:
        con.executemany(
            "INSERT INTO trka_items (id, name, ts) VALUES (?, ?, ?)",
            [("1", "a", "2026-01-01"), ("2", "b", "2026-01-02"), ("3", "c", "2026-01-03")],
        )
        con.commit()
    finally:
        con.close()

    r = _client(cfg).get("/api/v1/data/trka/trka_items")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["rows"]) == 3


def test_uninstalled_tracker_404s_for_api_and_page(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = _client(cfg)
    assert client.get("/api/v1/data/does_not_exist").status_code == 404
    assert client.get("/api/v1/data/does_not_exist/some_table").status_code == 404
    assert client.get("/t/does_not_exist/data").status_code == 404


def test_page_route_renders_picker_and_grid_payload(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("a", "2026-01-01"), ("b", "2026-01-02")])
    r = _client(cfg).get("/t/trka/data")
    assert r.status_code == 200
    assert 'data-data-table-btn="trka_items"' in r.text
    assert "data-pdb-grid" in r.text
    assert "2 rows · showing 1–2" in r.text
    assert "/static/pdb-data.js?v=2" in r.text


def test_page_route_shows_empty_state_for_zero_row_table(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    r = _client(cfg).get("/t/trka/data")
    assert r.status_code == 200
    assert "No data yet." in r.text
    assert "data-pdb-grid" not in r.text


def test_page_route_table_query_param_selects_requested_table(tmp_root):
    schema = _TRK_A_SCHEMA + " CREATE TABLE IF NOT EXISTS trka_other (id INTEGER PRIMARY KEY, name TEXT, ts TEXT);"
    cfg = _install_fixture_tracker(tmp_root, "trka", schema, "trka_items")
    _insert_rows(cfg, "trka_items", [("a", "2026-01-01")])
    r = _client(cfg).get("/t/trka/data?table=trka_other")
    assert r.status_code == 200
    assert 'data-table="trka_other"' in r.text
