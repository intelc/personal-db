"""Tests for the read-only per-tracker data navigator.

Covers GET /api/v1/data/<tracker>, GET /api/v1/data/<tracker>/<table> (incl.
sort/dir/q), GET /api/v1/data/<tracker>/<table>/row, and the GET
/t/<tracker>/data page route (src/personal_db/services/daemon/routes/data.py
+ data_browser.html). The central guarantee under test is that `table` (and
now `sort`) are always validated against the tracker's *installed*
schema.sql / PRAGMA table_info before they can reach SQL -- including a
table that legitimately exists in db.sqlite but belongs to a different
tracker.
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


def _install_fixture_tracker(
    tmp_root, name: str, schema_sql: str, table: str, *, apply_schema: bool = True
) -> Config:
    """Write a minimal, self-contained tracker (manifest + schema.sql + ingest.py)
    directly under <root>/trackers/<name>, matching the pattern other daemon
    route tests use (test_daemon_routes.py's `_make_runnable`) rather than
    depending on a bundled template's exact schema shape.

    `apply_schema=False` leaves the table declared (in schema.sql and the
    manifest) but never materialized in db.sqlite -- covers the "installed,
    not yet synced" degrade-gracefully path.
    """
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
    if apply_schema:
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
    assert body == {
        "tables": [
            {
                "name": "trka_items",
                "row_count": 3,
                "columns": [
                    {"name": "id", "type": "INTEGER", "semantic": "id"},
                    {"name": "name", "type": "TEXT", "semantic": None},
                    {"name": "ts", "type": "TEXT", "semantic": "ts"},
                ],
                "time_column": "ts",
                "time_range": {"min": "2026-01-01", "max": "2026-01-03"},
            }
        ]
    }


def test_api_data_tables_degrades_gracefully_for_unmaterialized_table(tmp_root):
    """Table is declared in schema.sql + manifest but schema.sql was never
    applied (installed, not yet synced) -- row_count 0, PRAGMA-less columns
    from the manifest, no time_range, never a 500."""
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items", apply_schema=False)
    r = _client(cfg).get("/api/v1/data/trka")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "tables": [
            {
                "name": "trka_items",
                "row_count": 0,
                "columns": [
                    {"name": "id", "type": "INTEGER", "semantic": "id"},
                    {"name": "ts", "type": "TEXT", "semantic": "ts"},
                ],
                "time_column": None,
                "time_range": None,
            }
        ]
    }


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
    # `id INTEGER PRIMARY KEY` is a rowid alias -- rowids line up 3, 2, 1.
    assert body["rowids"] == [3, 2, 1]


def test_sort_by_valid_column(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("first", "2026-01-03"), ("second", "2026-01-01"), ("third", "2026-01-02")])

    r = _client(cfg).get("/api/v1/data/trka/trka_items?sort=ts&dir=asc")
    assert r.status_code == 200
    body = r.json()
    assert [row[1] for row in body["rows"]] == ["second", "third", "first"]

    r = _client(cfg).get("/api/v1/data/trka/trka_items?sort=ts&dir=desc")
    assert r.status_code == 200
    body = r.json()
    assert [row[1] for row in body["rows"]] == ["first", "third", "second"]


def test_sort_by_invalid_column_is_rejected(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("a", "2026-01-01")])

    r = _client(cfg).get("/api/v1/data/trka/trka_items?sort=not_a_column")
    assert r.status_code == 400

    # SQL-injection-shaped sort value -- must 400, not error out on quoting.
    evil = urllib.parse.quote('ts"; DROP TABLE trka_items;--', safe="")
    r = _client(cfg).get(f"/api/v1/data/trka/trka_items?sort={evil}")
    assert r.status_code == 400

    con = sqlite3.connect(cfg.db_path)
    try:
        (count,) = con.execute("SELECT COUNT(*) FROM trka_items").fetchone()
    finally:
        con.close()
    assert count == 1


def test_sort_direction_must_be_asc_or_desc(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("a", "2026-01-01")])

    r = _client(cfg).get("/api/v1/data/trka/trka_items?sort=ts&dir=sideways")
    assert r.status_code == 400

    r = _client(cfg).get("/api/v1/data/trka/trka_items?dir=asc")
    assert r.status_code == 200


def test_q_filtering_changes_total(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(
        cfg,
        "trka_items",
        [("apple pie", "2026-01-01"), ("banana bread", "2026-01-02"), ("apple tart", "2026-01-03")],
    )

    r = _client(cfg).get("/api/v1/data/trka/trka_items")
    assert r.json()["total"] == 3

    r = _client(cfg).get("/api/v1/data/trka/trka_items?q=apple")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all("apple" in row[1] for row in body["rows"])

    r = _client(cfg).get("/api/v1/data/trka/trka_items?q=APPLE")
    assert r.json()["total"] == 2  # case-insensitive

    r = _client(cfg).get("/api/v1/data/trka/trka_items?q=nonexistent")
    assert r.json()["total"] == 0


def test_q_with_percent_and_underscore_literals(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(
        cfg,
        "trka_items",
        [("100% done", "2026-01-01"), ("100x done", "2026-01-02"), ("snake_case", "2026-01-03")],
    )

    # A literal "%" in the query must not act as a SQL LIKE wildcard.
    r = _client(cfg).get("/api/v1/data/trka/trka_items?q=" + urllib.parse.quote("100%"))
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0][1] == "100% done"

    # A literal "_" must not act as a SQL LIKE single-char wildcard (it would
    # otherwise also match "100x done").
    r = _client(cfg).get("/api/v1/data/trka/trka_items?q=" + urllib.parse.quote("snake_case"))
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0][1] == "snake_case"


def test_row_endpoint_happy_path(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("first", "2026-01-01"), ("second", "2026-01-02")])

    listing = _client(cfg).get("/api/v1/data/trka/trka_items").json()
    rowid = listing["rowids"][0]

    r = _client(cfg).get(f"/api/v1/data/trka/trka_items/row?rowid={rowid}")
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["id", "name", "ts"]
    assert body["row"][1] == "second"


def test_row_endpoint_missing_rowid_404s(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("a", "2026-01-01")])

    r = _client(cfg).get("/api/v1/data/trka/trka_items/row?rowid=999999")
    assert r.status_code == 404


def test_row_endpoint_rejects_unknown_table(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    r = _client(cfg).get("/api/v1/data/trka/not_a_real_table/row?rowid=1")
    assert r.status_code == 404


def test_row_endpoint_without_rowid_table_404s(tmp_root):
    schema = (
        "CREATE TABLE IF NOT EXISTS trka_items "
        "(id TEXT PRIMARY KEY, name TEXT, ts TEXT) WITHOUT ROWID;"
    )
    cfg = _install_fixture_tracker(tmp_root, "trka", schema, "trka_items")
    con = sqlite3.connect(cfg.db_path)
    try:
        con.execute("INSERT INTO trka_items (id, name, ts) VALUES ('1', 'a', '2026-01-01')")
        con.commit()
    finally:
        con.close()

    listing = _client(cfg).get("/api/v1/data/trka/trka_items").json()
    assert listing["rowids"] is None

    r = _client(cfg).get("/api/v1/data/trka/trka_items/row?rowid=1")
    assert r.status_code == 404


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
    assert body["rowids"] is None


def test_uninstalled_tracker_404s_for_api_and_page(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    client = _client(cfg)
    assert client.get("/api/v1/data/does_not_exist").status_code == 404
    assert client.get("/api/v1/data/does_not_exist/some_table").status_code == 404
    assert client.get("/api/v1/data/does_not_exist/some_table/row?rowid=1").status_code == 404
    assert client.get("/t/does_not_exist/data").status_code == 404


def test_page_route_renders_picker_and_grid_payload(tmp_root):
    cfg = _install_fixture_tracker(tmp_root, "trka", _TRK_A_SCHEMA, "trka_items")
    _insert_rows(cfg, "trka_items", [("a", "2026-01-01"), ("b", "2026-01-02")])
    r = _client(cfg).get("/t/trka/data")
    assert r.status_code == 200
    assert 'data-data-table-btn="trka_items"' in r.text
    assert "data-pdb-grid" in r.text
    assert "2 rows · showing 1–2" in r.text
    assert "/static/pdb-data.js?v=5" in r.text
    # Default sort is the manifest's time_column, descending.
    assert 'data-sort="ts"' in r.text
    assert "data-data-browser-tables" in r.text
    assert "data-data-search" in r.text
    # Stats line: time range + column count.
    assert "2026-01-01 → 2026-01-02 · 3 columns" in r.text


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
