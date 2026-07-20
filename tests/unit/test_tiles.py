"""Tests for the tile gallery: services/ui/tiles.py's loader, GET /api/v1/tiles,
and the "/" route rendering tile shells (dashboard_tiles.html).

Covers the metrics contract (installed visualizations.py exporting
`metrics(cfg) -> list[dict]`), the mechanical fallback for trackers without
one (or whose metrics() call raises), per-tracker exception isolation, the
failing/stale status derivation shared with /health, and the TTL cache.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import yaml
from fastapi.testclient import TestClient

from personal_db.core.config import Config
from personal_db.core.data_horizon import compute_and_store
from personal_db.core.db import apply_tracker_schema, init_db
from personal_db.core.manifest import load_manifest
from personal_db.services.daemon.http import build_app
from personal_db.services.ui import tiles as tiles_mod
from personal_db.services.ui.tiles import build_tiles, get_tiles
from tests._daemon_auth import auth_headers
from tests._validation_helpers import mark_valid


def _make_tracker(
    tmp_root,
    name,
    *,
    metrics_src: str | None = None,
    local_only: bool = False,
) -> Config:
    """Hand-build a minimal, runnable tracker dir + one table in db.sqlite.

    `metrics_src`, if given, is written verbatim as the tracker's
    `visualizations.py` (so tests can supply any `metrics(cfg)` body,
    including a deliberately broken one). Without it, no visualizations.py
    is written at all -- exercising the "no metrics() to call" fallback path.
    """
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": name,
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": "1h"},
                "time_column": "ts",
                "granularity": "event",
                "local_only": local_only,
                "schema": {
                    "tables": {
                        name: {
                            "columns": {
                                "id": {"type": "TEXT", "semantic": "id"},
                                "ts": {"type": "TEXT", "semantic": "ts"},
                            }
                        }
                    }
                },
            }
        )
    )
    schema_sql = f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, ts TEXT);"
    (d / "schema.sql").write_text(schema_sql)
    (d / "ingest.py").write_text("def backfill(t, start, end):\n    pass\ndef sync(t):\n    pass\n")
    if metrics_src is not None:
        (d / "visualizations.py").write_text(metrics_src)
    apply_tracker_schema(cfg.db_path, schema_sql)
    mark_valid(cfg, name)
    return cfg


def _insert_row(cfg: Config, name: str, row_id: str, ts: str) -> None:
    import sqlite3

    con = sqlite3.connect(cfg.db_path)
    con.execute(f"INSERT INTO {name} (id, ts) VALUES (?, ?)", (row_id, ts))
    con.commit()
    con.close()


def _make_multi_table_tracker(tmp_root, name: str, *, time_column: str) -> Config:
    """Hand-build a multi-table tracker reproducing the real monarch/plaid/
    crypto_wallet shape: the FIRST declared table has no `time_column` column
    at all (monarch_accounts/plaid_items/crypto_wallet_wallets are pure
    metadata tables), and a LATER table does. `_fallback_metrics` used to
    always query `time_column` against the first table regardless -- since
    that table lacks the column entirely, SQLite's "unresolvable quoted
    identifier becomes a string literal" behavior made `MAX("<time_column>")`
    return the column NAME itself rather than raising or returning NULL.
    """
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    meta_table = f"{name}_meta"
    events_table = f"{name}_events"
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": name,
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": "1h"},
                "time_column": time_column,
                "granularity": "event",
                "schema": {
                    "tables": {
                        # First table: no time_column at all (matches
                        # monarch_accounts / plaid_items / crypto_wallet_wallets).
                        meta_table: {
                            "columns": {"id": {"type": "TEXT", "semantic": "id"}}
                        },
                        # Second table: actually has time_column.
                        events_table: {
                            "columns": {
                                "id": {"type": "TEXT", "semantic": "id"},
                                time_column: {"type": "TEXT", "semantic": "ts"},
                            }
                        },
                    }
                },
            }
        )
    )
    schema_sql = (
        f"CREATE TABLE IF NOT EXISTS {meta_table} (id TEXT PRIMARY KEY);\n"
        f'CREATE TABLE IF NOT EXISTS {events_table} (id TEXT PRIMARY KEY, "{time_column}" TEXT);'
    )
    (d / "schema.sql").write_text(schema_sql)
    (d / "ingest.py").write_text("def backfill(t, start, end):\n    pass\ndef sync(t):\n    pass\n")
    apply_tracker_schema(cfg.db_path, schema_sql)
    mark_valid(cfg, name)
    return cfg, meta_table, events_table


def _insert_into(cfg: Config, table: str, columns: dict) -> None:
    import sqlite3

    con = sqlite3.connect(cfg.db_path)
    cols = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    con.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({placeholders})', tuple(columns.values()))
    con.commit()
    con.close()


def _mark_synced(cfg: Config, name: str) -> None:
    """Record a just-now successful sync so this tracker's status is "ok"
    (a never-synced tracker is "stale" by definition -- see `_is_stale`).
    Only needed by tests that aren't themselves exercising status derivation."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    last_run_path = cfg.state_dir / "last_run.json"
    data = {}
    if last_run_path.exists():
        data = json.loads(last_run_path.read_text())
    data[name] = datetime.now(UTC).isoformat()
    last_run_path.write_text(json.dumps(data))


# ---------- metrics contract ----------


def test_build_tiles_uses_custom_metrics(tmp_path):
    metrics_src = (
        "def metrics(cfg):\n"
        "    return [\n"
        "        {'label': 'hours', 'value': '5.9h', 'detail': 'top app · Cursor',\n"
        "         'delta': '+8% vs last week', 'good': True},\n"
        "        {'label': 'sessions', 'value': 12, 'detail': None,\n"
        "         'delta': None, 'good': None},\n"
        "    ]\n"
    )
    cfg = _make_tracker(tmp_path, "custom", metrics_src=metrics_src)
    _mark_synced(cfg, "custom")
    tiles = build_tiles(cfg)
    assert len(tiles) == 1
    tile = tiles[0]
    assert tile["slug"] == "custom"
    assert tile["href"] == "/t/custom"
    assert tile["status"] == "ok"
    assert len(tile["metrics"]) == 2
    m0, m1 = tile["metrics"]
    assert m0 == {
        "label": "hours",
        "value": "5.9h",
        "detail": "top app · Cursor",
        "delta": "+8% vs last week",
        "good": True,
    }
    # Non-string value coerced to str; missing detail/delta -> None.
    assert m1["value"] == "12"
    assert m1["detail"] is None
    assert m1["delta"] is None
    assert m1["good"] is None


def test_build_tiles_caps_at_four_metrics(tmp_path):
    metrics_src = (
        "def metrics(cfg):\n"
        "    return [{'label': str(i), 'value': str(i)} for i in range(7)]\n"
    )
    cfg = _make_tracker(tmp_path, "many", metrics_src=metrics_src)
    tile = build_tiles(cfg)[0]
    assert len(tile["metrics"]) == 4
    assert [m["label"] for m in tile["metrics"]] == ["0", "1", "2", "3"]


def test_build_tiles_filters_invalid_metric_entries(tmp_path):
    metrics_src = (
        "def metrics(cfg):\n"
        "    return [\n"
        "        {'label': 'ok', 'value': '1'},\n"
        "        {'value': 'missing label'},\n"
        "        'not a dict',\n"
        "        None,\n"
        "    ]\n"
    )
    cfg = _make_tracker(tmp_path, "dirty", metrics_src=metrics_src)
    tile = build_tiles(cfg)[0]
    assert tile["metrics"] == [
        {"label": "ok", "value": "1", "detail": None, "delta": None, "good": None}
    ]


# ---------- fallback path ----------


def test_build_tiles_falls_back_without_visualizations_file(tmp_path):
    cfg = _make_tracker(tmp_path, "plain")
    _insert_row(cfg, "plain", "a", "2026-07-01T00:00:00+00:00")
    _insert_row(cfg, "plain", "b", "2026-07-10T00:00:00+00:00")
    tile = build_tiles(cfg)[0]
    assert tile["metrics"]
    by_label = {m["label"]: m["value"] for m in tile["metrics"]}
    assert by_label["rows"] == "2"


def test_build_tiles_falls_back_when_metrics_raises(tmp_path):
    metrics_src = "def metrics(cfg):\n    raise RuntimeError('boom')\n"
    cfg = _make_tracker(tmp_path, "broken", metrics_src=metrics_src)
    _insert_row(cfg, "broken", "a", "2026-07-01T00:00:00+00:00")
    tile = build_tiles(cfg)[0]
    # Falls back to mechanical metrics rather than an empty/broken tile.
    by_label = {m["label"]: m["value"] for m in tile["metrics"]}
    assert by_label["rows"] == "1"


def test_build_tiles_falls_back_when_metrics_returns_non_list(tmp_path):
    metrics_src = "def metrics(cfg):\n    return {'not': 'a list'}\n"
    cfg = _make_tracker(tmp_path, "wrongtype", metrics_src=metrics_src)
    _insert_row(cfg, "wrongtype", "a", "2026-07-01T00:00:00+00:00")
    tile = build_tiles(cfg)[0]
    by_label = {m["label"]: m["value"] for m in tile["metrics"]}
    assert by_label["rows"] == "1"


def test_build_tiles_fallback_latest_handles_future_timestamps(tmp_path):
    """A tracker whose rows carry forward-looking timestamps (e.g. `calendar`'s
    upcoming events) can have MAX(time_column) in the future -- humanize_age
    has no concept of a negative duration, so the fallback must not hand it
    one (regression: used to render "-171927655s ago")."""
    cfg = _make_tracker(tmp_path, "future_events")
    future_ts = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    _insert_row(cfg, "future_events", "a", future_ts)
    tile = build_tiles(cfg)[0]
    by_label = {m["label"]: m["value"] for m in tile["metrics"]}
    assert "ago" not in by_label["latest"]
    assert by_label["latest"] == future_ts[:10]


def test_build_tiles_fallback_finds_time_column_in_non_first_table(tmp_path):
    """Regression: monarch/plaid/crypto_wallet-shaped trackers (first table
    is metadata-only, has no `time_column`; a later table does) used to
    render the column NAME itself ("date", "fetched_at") as the "latest"
    value instead of a real timestamp -- see `_make_multi_table_tracker`'s
    docstring for the SQLite quirk that caused it."""
    cfg, meta_table, events_table = _make_multi_table_tracker(
        tmp_path, "multitable", time_column="date"
    )
    _insert_into(cfg, meta_table, {"id": "m1"})
    _insert_into(cfg, events_table, {"id": "e1", "date": "2026-05-01T00:00:00+00:00"})
    _insert_into(cfg, events_table, {"id": "e2", "date": "2026-06-15T00:00:00+00:00"})
    tile = build_tiles(cfg)[0]
    by_label = {m["label"]: m["value"] for m in tile["metrics"]}
    assert by_label["rows"] == "3"  # 1 meta row + 2 event rows
    assert by_label["latest"] != "date"
    # Newest across the events table (2026-06-15), not the meta table (which
    # has no `date` column at all) and not the older 2026-05-01 row.
    newest_dt = datetime.fromisoformat("2026-06-15T00:00:00+00:00")
    expected = tiles_mod.humanize_age(datetime.now(UTC) - newest_dt)
    assert by_label["latest"] == expected


def test_build_tiles_fallback_time_column_named_fetched_at(tmp_path):
    """Same regression, but for the `fetched_at` time_column shape
    (crypto_wallet) -- used to render the literal string "fetched_at"."""
    cfg, meta_table, events_table = _make_multi_table_tracker(
        tmp_path, "walletish", time_column="fetched_at"
    )
    _insert_into(cfg, meta_table, {"id": "w1"})
    _insert_into(cfg, events_table, {"id": "b1", "fetched_at": "2026-04-01T00:00:00+00:00"})
    tile = build_tiles(cfg)[0]
    by_label = {m["label"]: m["value"] for m in tile["metrics"]}
    assert by_label["latest"] != "fetched_at"
    assert "ago" in by_label["latest"] or by_label["latest"] == "2026-04-01"


def test_build_tiles_fallback_includes_data_horizon_for_local_only(tmp_path):
    cfg = _make_tracker(tmp_path, "horizoned", local_only=True)
    _insert_row(cfg, "horizoned", "a", "2020-01-01T00:00:00+00:00")
    manifest = load_manifest(cfg.trackers_dir / "horizoned" / "manifest.yaml")
    compute_and_store(cfg, "horizoned", manifest)
    tile = build_tiles(cfg)[0]
    labels = [m["label"] for m in tile["metrics"]]
    assert "data since" in labels


def test_build_tiles_fallback_empty_table_has_zero_rows(tmp_path):
    cfg = _make_tracker(tmp_path, "empty")
    tile = build_tiles(cfg)[0]
    by_label = {m["label"]: m["value"] for m in tile["metrics"]}
    assert by_label["rows"] == "0"


def test_build_tiles_no_installed_trackers_is_empty(tmp_path):
    cfg = Config(root=tmp_path)
    init_db(cfg.db_path)
    assert build_tiles(cfg) == []


# ---------- status derivation (shared with tracker_status_map / /health) ----------


def test_build_tiles_status_failing_surfaces_error_first_line(tmp_path):
    cfg = _make_tracker(tmp_path, "flaky")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    with (cfg.state_dir / "sync_errors.jsonl").open("w") as f:
        f.write(
            json.dumps(
                {
                    "ts": now.isoformat(),
                    "tracker": "flaky",
                    "error": "boom: connection reset\nmore detail",
                    "tb": "",
                }
            )
            + "\n"
        )
    tile = build_tiles(cfg)[0]
    assert tile["status"] == "failing"
    assert tile["status_detail"] == "boom: connection reset"


def test_build_tiles_status_stale_reports_age(tmp_path):
    cfg = _make_tracker(tmp_path, "stale_one")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    old = datetime.now(UTC) - timedelta(days=10)
    (cfg.state_dir / "last_run.json").write_text(
        json.dumps({"stale_one": old.isoformat()})
    )
    tile = build_tiles(cfg)[0]
    assert tile["status"] == "stale"
    assert "no new data in" in tile["status_detail"]


def test_build_tiles_status_ok_has_no_detail(tmp_path):
    cfg = _make_tracker(tmp_path, "healthy")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "last_run.json").write_text(
        json.dumps({"healthy": datetime.now(UTC).isoformat()})
    )
    tile = build_tiles(cfg)[0]
    assert tile["status"] == "ok"
    assert tile["status_detail"] is None


# ---------- TTL cache ----------


def test_get_tiles_caches_within_ttl(tmp_path, monkeypatch):
    cfg = _make_tracker(tmp_path, "cached")
    monkeypatch.setattr(tiles_mod, "_cache", {})
    first = get_tiles(cfg)
    _insert_row(cfg, "cached", "a", "2026-07-01T00:00:00+00:00")
    second = get_tiles(cfg)
    assert second == first  # still cached, insert not reflected yet

    third = get_tiles(cfg, force=True)
    by_label = {m["label"]: m["value"] for m in third[0]["metrics"]}
    assert by_label["rows"] == "1"


def test_get_tiles_expires_after_ttl(tmp_path, monkeypatch):
    cfg = _make_tracker(tmp_path, "expiring")
    monkeypatch.setattr(tiles_mod, "_cache", {})
    fake_time = [1000.0]
    monkeypatch.setattr(tiles_mod.time, "monotonic", lambda: fake_time[0])
    get_tiles(cfg)
    _insert_row(cfg, "expiring", "a", "2026-07-01T00:00:00+00:00")
    fake_time[0] += tiles_mod._CACHE_TTL_SECONDS + 1
    refreshed = get_tiles(cfg)
    by_label = {m["label"]: m["value"] for m in refreshed[0]["metrics"]}
    assert by_label["rows"] == "1"


# ---------- endpoint + route ----------


def test_tiles_endpoint_shape(tmp_path):
    metrics_src = (
        "def metrics(cfg):\n"
        "    return [{'label': 'rows', 'value': '3', 'detail': None,\n"
        "             'delta': '+1', 'good': True}]\n"
    )
    cfg = _make_tracker(tmp_path, "api_tiles", metrics_src=metrics_src)
    _mark_synced(cfg, "api_tiles")
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/api/v1/tiles")
    assert r.status_code == 200
    body = r.json()
    assert "tiles" in body
    assert len(body["tiles"]) == 1
    tile = body["tiles"][0]
    assert tile["slug"] == "api_tiles"
    assert tile["href"] == "/t/api_tiles"
    assert tile["status"] == "ok"
    assert tile["metrics"][0]["value"] == "3"


def test_dashboard_route_renders_tile_shells(tmp_path):
    metrics_src = (
        "def metrics(cfg):\n"
        "    return [{'label': 'hours', 'value': '5.9h'},\n"
        "            {'label': 'sessions', 'value': '12'}]\n"
    )
    cfg = _make_tracker(tmp_path, "galleryone", metrics_src=metrics_src)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert 'class="tile-gallery"' in r.text
    assert 'data-slug="galleryone"' in r.text
    # First metric server-rendered as a shell (no-JS still shows something).
    assert "5.9h" in r.text
    assert "hours" in r.text
    # Dots for the second metric (rotation target) are present.
    assert 'data-tile-dot' in r.text
    # Inline hydration payload for pdb-tiles.js.
    assert 'id="pdb-tiles-data"' in r.text
    assert "sessions" in r.text


def test_dashboard_route_marks_failing_tile(tmp_path):
    cfg = _make_tracker(tmp_path, "brokentracker")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with (cfg.state_dir / "sync_errors.jsonl").open("w") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "tracker": "brokentracker",
                    "error": "auth expired",
                    "tb": "",
                }
            )
            + "\n"
        )
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "tile-status-failing" in r.text
    assert "auth expired" in r.text


def test_dashboard_route_shows_welcome_hero_with_no_trackers(tmp_path):
    cfg = Config(root=tmp_path)
    init_db(cfg.db_path)
    client = TestClient(build_app(cfg), headers=auth_headers(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "Connect your first source" in r.text
    assert 'class="tile-gallery"' not in r.text
