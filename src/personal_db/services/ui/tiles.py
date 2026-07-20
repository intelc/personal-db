"""Dashboard tile gallery loader: per-tracker/per-app headline metrics for `/`.

Each installed tracker gets one tile. If its *installed* `visualizations.py`
exports `metrics(cfg) -> list[dict]` (see the module docstring on that
contract -- up to 4 of `{label, value, detail, delta, good, sensitive}`),
this loader calls it and uses those, coerced to strings and capped at 4.
Otherwise -- including when the call raises -- it falls back to
mechanically-derived metrics: total row count across the tracker's tables,
the newest recorded event (humanized age), and the tracker's recorded data
horizon (for `local_only` trackers that track one).

Apps (services/daemon's `_app_registry` / `core.apps.discover_apps`) get a
tile too, ahead of tracker tiles, but only if their `views.py` exports a
`metrics(cfg) -> list[dict]` -- apps have no sync state and no schema.sql
table inventory to fall back to mechanically, so an app without `metrics()`
gets no tile at all rather than a fabricated one.

Every step is isolated per-tracker/per-app: one whose `metrics()` raises, or
whose fallback derivation hits a missing table, still gets a tile -- it just
falls back further (custom metrics -> mechanical metrics -> empty list, or
for apps, custom metrics -> no tile) rather than ever taking the whole `/`
route down with it.

Results are cached in-process for `_CACHE_TTL_SECONDS`, keyed by root path,
so a burst of requests (initial page load + pdb-tiles.js's periodic
refresh) doesn't re-run sqlite queries and re-import every tracker's/app's
module on each one.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from personal_db.core.apps import AppDefinition, discover_apps, load_app_module
from personal_db.core.config import Config
from personal_db.core.data_horizon import get as _get_horizon
from personal_db.core.manifest import ManifestError, humanize_tracker_name, load_manifest
from personal_db.core.sync import tracker_schema_tables
from personal_db.services.ui.builtin_viz import humanize_age, tracker_status_map
from personal_db.services.ui.viz import _load_module

_CACHE_TTL_SECONDS = 60
_MAX_METRICS = 4

# {root_path: (monotonic_computed_at, tiles)}
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _installed_trackers(cfg: Config) -> list[str]:
    """Same discovery `build_health_page_data`/`tracker_status_map` use."""
    if not cfg.trackers_dir.exists():
        return []
    return sorted(
        d.name
        for d in cfg.trackers_dir.iterdir()
        if d.is_dir() and (d / "manifest.yaml").exists()
    )


def _coerce_metric(raw: Any) -> dict[str, Any] | None:
    """Validate + coerce one metrics() entry to the fixed contract shape.

    `label`/`value` are required (missing either drops the entry); `detail`/
    `delta` are coerced to `str` when present, `None` otherwise; `good` is
    passed through only if it's already a real bool (anything else -> None,
    since it drives color, not display text -- no reason to coerce it to a
    string). `sensitive` is coerced to `bool`, defaulting `False` -- it
    drives the discreet-mode blur (see `.pdb-sensitive` in style.css /
    pdb-tiles.js), not display text either.
    """
    if not isinstance(raw, dict):
        return None
    label = raw.get("label")
    value = raw.get("value")
    if label is None or value is None:
        return None
    detail = raw.get("detail")
    delta = raw.get("delta")
    good = raw.get("good")
    return {
        "label": str(label),
        "value": str(value),
        "detail": str(detail) if detail is not None else None,
        "delta": str(delta) if delta is not None else None,
        "good": good if isinstance(good, bool) else None,
        "sensitive": bool(raw.get("sensitive", False)),
    }


def _custom_metrics(cfg: Config, tracker: str) -> list[dict[str, Any]] | None:
    """Call the installed tracker's `metrics(cfg)`, if it has one.

    Returns `None` (not `[]`) when there's no visualizations.py, no
    `metrics` function, the call raises, or it returns nothing usable --
    callers fall back to mechanically-derived metrics in every one of those
    cases. Never lets an exception escape.
    """
    viz_path = cfg.trackers_dir / tracker / "visualizations.py"
    if not viz_path.is_file():
        return None
    try:
        mod = _load_module(viz_path, f"personal_db_tiles_{tracker}")
        fn = getattr(mod, "metrics", None)
        if fn is None:
            return None
        raw_metrics = fn(cfg)
        if not isinstance(raw_metrics, list):
            return None
        out: list[dict[str, Any]] = []
        for raw in raw_metrics[:_MAX_METRICS]:
            coerced = _coerce_metric(raw)
            if coerced is not None:
                out.append(coerced)
        return out or None
    except Exception:  # noqa: BLE001 — isolate one tracker's bad metrics() from the rest
        return None


def _neutral_metric(label: str, value: str) -> dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "detail": None,
        "delta": None,
        "good": None,
        "sensitive": False,
    }


def _fallback_metrics(cfg: Config, tracker: str) -> list[dict[str, Any]]:
    """Mechanically-derived metrics when a tracker has no (working) `metrics()`.

    Total row count across every table its installed schema.sql declares,
    the newest recorded event (humanized age) off the primary table's time
    column, and its recorded data horizon (for `local_only` trackers).
    Missing pieces (no db yet, empty tables, no horizon) are just omitted --
    this never raises, it just returns however much it could derive.
    """
    metrics: list[dict[str, Any]] = []

    try:
        manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
    except ManifestError:
        return metrics

    schema_path = cfg.trackers_dir / tracker / "schema.sql"
    if schema_path.is_file():
        try:
            schema_tables = tracker_schema_tables(schema_path.read_text())
        except Exception:  # noqa: BLE001
            schema_tables = set(manifest.schema.tables)
    else:
        schema_tables = set(manifest.schema.tables)

    time_col = manifest.time_column

    total_rows = 0
    newest: str | None = None
    if cfg.db_path.exists() and schema_tables:
        con: sqlite3.Connection | None
        try:
            con = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
            con.execute("PRAGMA query_only = ON")
        except sqlite3.OperationalError:
            con = None
        if con is not None:
            try:
                for table in schema_tables:
                    try:
                        (count,) = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                        total_rows += int(count)
                    except sqlite3.OperationalError:
                        continue
                    # Newest event across every table that actually HAS
                    # `time_column`, checked against the live schema (PRAGMA
                    # table_info, not just the first table in the manifest,
                    # and not just the manifest's declared columns either --
                    # multi-table trackers (monarch, plaid, crypto_wallet)
                    # don't put a time-bearing table first, several tables
                    # can share the same column name, and installed
                    # schema.sql can drift from the manifest). Querying
                    # MAX("<col>") against a table that lacks that column
                    # doesn't raise -- SQLite falls back to treating an
                    # unresolvable quoted identifier as a string literal, so
                    # the "newest event" would silently become the column
                    # NAME itself (e.g. "date", "fetched_at") instead of a
                    # real value or an error. Only ever query tables
                    # confirmed (via introspection) to have the column.
                    try:
                        cols = {
                            row[1]
                            for row in con.execute(f'PRAGMA table_info("{table}")').fetchall()
                        }
                    except sqlite3.OperationalError:
                        continue
                    if time_col not in cols:
                        continue
                    try:
                        row = con.execute(
                            f'SELECT MAX("{time_col}") FROM "{table}"'
                        ).fetchone()
                    except sqlite3.OperationalError:
                        continue
                    value = row[0] if row else None
                    if value and (newest is None or value > newest):
                        newest = value
            finally:
                con.close()

    metrics.append(_neutral_metric("rows", f"{total_rows:,}"))

    if newest:
        # Default to the raw date; only replace it with a humanized "Nh ago"
        # when the parse succeeds AND the timestamp is actually in the past
        # -- MAX(time_column) can be in the future for trackers whose rows
        # carry forward-looking timestamps (e.g. `calendar`'s upcoming
        # events), and humanize_age has no concept of negative durations.
        age_text = newest[:10]
        try:
            newest_dt = datetime.fromisoformat(newest)
            if newest_dt.tzinfo is None:
                newest_dt = newest_dt.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - newest_dt
            if delta >= timedelta(0):
                age_text = humanize_age(delta)
        except ValueError:
            pass
        metrics.append(_neutral_metric("latest", age_text))

    horizon = _get_horizon(cfg, tracker)
    if horizon:
        metrics.append(_neutral_metric("data since", str(horizon)[:10]))

    return metrics[:_MAX_METRICS]


def _tile_status(status: dict[str, Any] | None) -> tuple[str, str | None]:
    """(status, status_detail) for one tracker, from `tracker_status_map`'s entry."""
    status = status or {}
    error = status.get("error")
    if error:
        return "failing", error.get("first_line")
    if status.get("stale"):
        age = status.get("last_sync_age")
        age_text = age.replace(" ago", "") if age else "a while"
        return "stale", f"no new data in {age_text}"
    return "ok", None


def _title_for(cfg: Config, tracker: str) -> str:
    try:
        return load_manifest(cfg.trackers_dir / tracker / "manifest.yaml").display_title()
    except ManifestError:
        return humanize_tracker_name(tracker)


def _custom_app_metrics(cfg: Config, definition: AppDefinition) -> list[dict[str, Any]] | None:
    """Call the app's `views.py`'s `metrics(cfg)`, if it has one.

    Mirrors `_custom_metrics` for trackers, but there is no mechanical
    fallback for apps (see module docstring): `None` here means the app
    simply gets no tile, not a fabricated row-count one. Never lets an
    exception escape -- one app's broken `metrics()` must not sink the
    whole gallery.
    """
    try:
        mod = load_app_module(definition.root, definition.name, "views")
        fn = getattr(mod, "metrics", None)
        if fn is None:
            return None
        raw_metrics = fn(cfg)
        if not isinstance(raw_metrics, list):
            return None
        out: list[dict[str, Any]] = []
        for raw in raw_metrics[:_MAX_METRICS]:
            coerced = _coerce_metric(raw)
            if coerced is not None:
                out.append(coerced)
        return out or None
    except Exception:  # noqa: BLE001 — isolate one app's bad metrics() from the rest
        return None


def build_app_tiles(cfg: Config) -> list[dict[str, Any]]:
    """One tile per installed app whose `views.py` exports a working
    `metrics(cfg)`. Apps without one get no tile at all -- see module
    docstring for why apps don't get the trackers' mechanical fallback.

    App tiles have no sync state (no daemon-tracked cursor/error history
    the way trackers do), so `status` is always "ok" / `status_detail` is
    always `None` here. `slug` is namespaced ("app:<name>") so it can never
    collide with a tracker's bare slug -- both the `finance` app and the
    `finance` tracker get tiles, deliberately showing different metrics
    (see templates/apps/finance/views.py vs templates/trackers/finance/
    visualizations.py), and pdb-tiles.js's hydration keys off this field.
    """
    tiles: list[dict[str, Any]] = []
    try:
        apps = discover_apps(cfg)
    except Exception:  # noqa: BLE001 — app discovery failure must not sink the gallery
        return tiles
    for name, definition in apps.items():
        try:
            metrics = _custom_app_metrics(cfg, definition)
        except Exception:  # noqa: BLE001
            metrics = None
        if not metrics:
            continue
        tiles.append(
            {
                "slug": f"app:{name}",
                "title": definition.manifest.title,
                "href": f"/a/{name}",
                "status": "ok",
                "status_detail": None,
                "metrics": metrics,
                "kind": "app",
            }
        )
    return tiles


def build_tiles(cfg: Config) -> list[dict[str, Any]]:
    """App tiles (those with a working `metrics()`) ahead of one tile per
    installed tracker. Never raises."""
    tiles: list[dict[str, Any]] = build_app_tiles(cfg)
    statuses = tracker_status_map(cfg)
    for tracker in _installed_trackers(cfg):
        title = _title_for(cfg, tracker)
        status, status_detail = "ok", None
        metrics: list[dict[str, Any]] = []
        try:
            status, status_detail = _tile_status(statuses.get(tracker))
            metrics = _custom_metrics(cfg, tracker) or _fallback_metrics(cfg, tracker)
        except Exception:  # noqa: BLE001 — one tracker's tile never sinks the gallery
            metrics = []
        tiles.append(
            {
                "slug": tracker,
                "title": title,
                "href": f"/t/{tracker}",
                "status": status,
                "status_detail": status_detail,
                "metrics": metrics,
                "kind": "tracker",
            }
        )
    return tiles


def get_tiles(cfg: Config, *, force: bool = False) -> list[dict[str, Any]]:
    """TTL-cached wrapper around `build_tiles` (60s, keyed by root path)."""
    key = str(cfg.root)
    now = time.monotonic()
    if not force:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
    tiles = build_tiles(cfg)
    _cache[key] = (now, tiles)
    return tiles
