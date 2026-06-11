from __future__ import annotations

import html
import json
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from personal_db.apps import AppContext
from personal_db.db import connect
from personal_db.ui import agcharts
from personal_db.ui import components as c
from personal_db.ui.charts import heatmap, horizontal_bars

_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
_ACCURACY_M_MAX = 100
_STOP_RADIUS_M = 50
_STOP_MIN_MINUTES = 5


def _style() -> str:
    return """
    <style>
      .places-leaflet-map {
        height: 620px;
        width: 100%;
        border: 1px solid #000;
        background: #f8fafc;
      }
      .places-two-col {
        display: grid;
        grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.65fr);
        gap: calc(var(--grid) * 2);
        align-items: start;
      }
      .places-settings-form,
      .places-alias-form {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: end;
        margin: var(--grid) 0;
      }
      .places-settings-form label,
      .places-alias-form label {
        display: grid;
        gap: 2px;
        font-size: 11px;
        color: #555;
      }
      .places-settings-form input,
      .places-alias-form input {
        min-height: 26px;
        border: 1px solid #000;
        padding: 2px 6px;
        font: inherit;
        font-size: 12px;
      }
      .places-settings-form button,
      .places-alias-form button {
        min-height: 26px;
        border: 1px solid #000;
        background: #fff;
        font: inherit;
        font-size: 12px;
        cursor: pointer;
      }
      .places-settings-form button:hover,
      .places-alias-form button:hover { background: #000; color: #fff; }
      .places-checkbox {
        display: flex !important;
        grid-template-columns: none !important;
        flex-direction: row;
        align-items: center;
        min-height: 26px;
        padding: 2px 6px;
        border: 1px solid #000;
        color: #000 !important;
      }
      .places-checkbox input { min-height: auto; }
      @media (max-width: 860px) {
        .places-two-col { grid-template-columns: 1fr; }
        .places-leaflet-map { height: 420px; }
      }
    </style>
    """


def _cutoff(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _nav(ctx: AppContext, active: str) -> list[tuple[str, str, bool]]:
    return [
        (page.title, f"/a/{ctx.manifest.name}/{page.slug}", page.slug == active)
        for page in ctx.manifest.pages
    ]


def _q(ctx: AppContext, name: str, **params: Any) -> list[dict[str, Any]]:
    try:
        return ctx.query(name, **params)
    except sqlite3.Error:
        return []


def _table_exists(ctx: AppContext, table: str) -> bool:
    try:
        con = connect(ctx.cfg.db_path, read_only=True)
        try:
            row = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            return row is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _columns(ctx: AppContext, table: str) -> set[str]:
    try:
        con = connect(ctx.cfg.db_path, read_only=True)
        try:
            return {str(row[1]) for row in con.execute(f'PRAGMA table_info("{table}")').fetchall()}
        finally:
            con.close()
    except sqlite3.Error:
        return set()


def _location_source(ctx: AppContext) -> str | None:
    if _table_exists(ctx, "location_points"):
        cols = _columns(ctx, "location_points")
        if {"recorded_at", "latitude", "longitude"}.issubset(cols):
            return "location_points"
    if _table_exists(ctx, "raw_locations"):
        cols = _columns(ctx, "raw_locations")
        if {"id", "ts", "lat", "lon"}.issubset(cols):
            return "raw_locations"
    return None


def _geocode_source(ctx: AppContext) -> str | None:
    if not _table_exists(ctx, "geocoded_locations"):
        return None
    cols = _columns(ctx, "geocoded_locations")
    if {"recorded_at", "formatted_address"}.issubset(cols):
        return "recorded_at"
    if {"source_id", "place_name"}.issubset(cols):
        return "source_id"
    return None


def _run(ctx: AppContext, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        con = connect(ctx.cfg.db_path, read_only=True)
        con.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in con.execute(sql, params).fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def _settings(ctx: AppContext) -> dict[str, str]:
    values = {"blur_precision_m": "0", "hide_coordinates": "0", "default_days": "30"}
    for row in _q(ctx, "settings"):
        values[str(row["key"])] = str(row["value"])
    return values


def _int_setting(settings: dict[str, str], key: str, default: int) -> int:
    try:
        return int(float(settings.get(key, str(default))))
    except (TypeError, ValueError):
        return default


def _alias_state(ctx: AppContext) -> dict[str, tuple[str, bool]]:
    out: dict[str, tuple[str, bool]] = {}
    for row in _q(ctx, "visible_aliases"):
        place = str(row.get("place_name") or "")
        if place:
            out[place] = (str(row.get("alias") or place), bool(row.get("hidden")))
    return out


def _display_place(place_name: str, aliases: dict[str, tuple[str, bool]]) -> str | None:
    alias, hidden = aliases.get(place_name, (place_name, False))
    if hidden:
        return None
    return alias


def _time(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        text = text.replace("T", " ")
    for suffix in ("+00:00", "Z"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.split(".", 1)[0]


def _short_time(value: Any) -> str:
    text = _time(value)
    return text[11:16] if len(text) >= 16 else text


def _duration(start: Any, end: Any) -> str:
    try:
        started = datetime.fromisoformat(str(start))
        ended = datetime.fromisoformat(str(end))
    except ValueError:
        return ""
    minutes = max(0, int((ended - started).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"


def _map_notice() -> str:
    return c.notice("Maps use exact local GPS coordinates with OpenStreetMap tiles.")


def _metrics(ctx: AppContext, aliases: dict[str, tuple[str, bool]]) -> list[tuple[str, str, str]]:
    source = _location_source(ctx)
    if source == "location_points":
        rows = _run(
            ctx,
            """
            SELECT min(recorded_at) AS first_ts,
                   max(recorded_at) AS last_ts,
                   count(*) AS points,
                   count(DISTINCT date(recorded_at, 'localtime')) AS days
            FROM location_points
            """,
        )
    elif source == "raw_locations":
        rows = _q(ctx, "raw_location_bounds")
    else:
        rows = []
    if not rows or rows[0].get("points") in (None, 0):
        return [
            ("Points", "0", "mobile export pending"),
            ("Days", "0", "no synced rows"),
            ("Places", "0", "no labels yet"),
            ("Last Seen", "none", ""),
        ]
    row = rows[0]
    top_places = _top_places(ctx, aliases, days=3650, limit=5000)
    return [
        ("Points", f"{int(row.get('points') or 0):,}", "raw local rows"),
        ("Days", f"{int(row.get('days') or 0):,}", "with location data"),
        ("Places", f"{len(top_places):,}", "visible labels"),
        ("Last Seen", _time(row.get("last_ts"))[:16] or "none", "local timeline"),
    ]


def _top_places(
    ctx: AppContext,
    aliases: dict[str, tuple[str, bool]],
    *,
    days: int,
    limit: int,
) -> list[dict[str, Any]]:
    location_source = _location_source(ctx)
    geocode_source = _geocode_source(ctx)
    if location_source == "location_points" and geocode_source == "recorded_at":
        rows = _run(
            ctx,
            """
            SELECT coalesce(g.formatted_address, '(unlabeled)') AS place_name,
                   count(*) AS points,
                   count(DISTINCT date(p.recorded_at, 'localtime')) AS days,
                   min(p.recorded_at) AS first_seen,
                   max(p.recorded_at) AS last_seen
            FROM location_points p
            LEFT JOIN geocoded_locations g ON g.recorded_at = p.recorded_at
            WHERE p.recorded_at >= ?
            GROUP BY coalesce(g.place_id, g.formatted_address, '(unlabeled)')
            ORDER BY points DESC
            LIMIT ?
            """,
            (_cutoff(days), limit),
        )
    elif location_source == "raw_locations" and geocode_source == "source_id":
        rows = _q(ctx, "top_places", cutoff=_cutoff(days), limit=limit)
    else:
        rows = _run(
            ctx,
            """
            SELECT '(unlabeled)' AS place_name,
                   count(*) AS points,
                   count(DISTINCT date(ts, 'localtime')) AS days,
                   min(ts) AS first_seen,
                   max(ts) AS last_seen
            FROM raw_locations
            WHERE ts >= ?
            LIMIT ?
            """,
            (_cutoff(days), limit),
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        place = str(row.get("place_name") or "(unlabeled)")
        display = _display_place(place, aliases)
        if not display:
            continue
        item = dict(row)
        item["place_name"] = display
        out.append(item)
    return out


def _raw_points(
    ctx: AppContext,
    aliases: dict[str, tuple[str, bool]],
    *,
    days: int,
    limit: int,
) -> list[dict[str, Any]]:
    location_source = _location_source(ctx)
    geocode_source = _geocode_source(ctx)
    if location_source == "location_points" and geocode_source == "recorded_at":
        rows = _run(
            ctx,
            """
            SELECT p.id,
                   p.recorded_at AS ts,
                   p.latitude AS lat,
                   p.longitude AS lon,
                   p.accuracy AS accuracy,
                   coalesce(g.formatted_address, '(unlabeled)') AS place_name
            FROM location_points p
            LEFT JOIN geocoded_locations g ON g.recorded_at = p.recorded_at
            WHERE p.recorded_at >= ?
            ORDER BY p.recorded_at ASC
            LIMIT ?
            """,
            (_cutoff(days), limit),
        )
    elif location_source == "location_points":
        rows = _run(
            ctx,
            """
            SELECT id,
                   recorded_at AS ts,
                   latitude AS lat,
                   longitude AS lon,
                   accuracy AS accuracy,
                   '(unlabeled)' AS place_name
            FROM location_points
            WHERE recorded_at >= ?
            ORDER BY recorded_at ASC
            LIMIT ?
            """,
            (_cutoff(days), limit),
        )
    elif location_source == "raw_locations" and geocode_source == "source_id":
        rows = _q(ctx, "recent_points", cutoff=_cutoff(days), limit=limit)
    else:
        rows = _run(
            ctx,
            """
            SELECT id, ts, lat, lon, NULL AS accuracy, '(unlabeled)' AS place_name
            FROM raw_locations
            WHERE ts >= ?
            ORDER BY ts ASC
            LIMIT ?
            """,
            (_cutoff(days), limit),
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        place = str(row.get("place_name") or "(unlabeled)")
        display = _display_place(place, aliases)
        if not display:
            continue
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (TypeError, ValueError):
            continue
        item = dict(row)
        item["lat"] = lat
        item["lon"] = lon
        item["place_name"] = display
        out.append(item)
    return out


def _js_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def _leaflet_loader(init_fn: str, *, needs_heat: bool = False) -> str:
    heat_loader = (
        "function withHeat(cb){if(window.L&&window.L.heatLayer)cb();"
        "else loadJs('https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js',"
        "'pdb-leaflet-heat-js',cb);}"
    )
    init_call = f"withLeaflet(function(){{withHeat({init_fn});}});"
    if not needs_heat:
        heat_loader = ""
        init_call = f"withLeaflet({init_fn});"
    return f"""
<script>
(function() {{
  function loadCss(url, id) {{
    if (document.getElementById(id)) return;
    var l = document.createElement('link');
    l.rel = 'stylesheet'; l.href = url; l.id = id;
    document.head.appendChild(l);
  }}
  function loadJs(url, id, cb) {{
    var existing = document.getElementById(id);
    if (existing) {{
      if (existing.dataset.loaded === '1') cb();
      else existing.addEventListener('load', cb);
      return;
    }}
    var s = document.createElement('script');
    s.src = url; s.id = id;
    s.addEventListener('load', function() {{ s.dataset.loaded = '1'; cb(); }});
    document.head.appendChild(s);
  }}
  function withLeaflet(cb) {{
    if (window.L) cb();
    else loadJs('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js', 'pdb-leaflet-js', cb);
  }}
  {heat_loader}
  loadCss('https://unpkg.com/leaflet@1.9.4/dist/leaflet.css', 'pdb-leaflet-css');
  {init_call}
}})();
</script>
"""


def _map_html(points: list[dict[str, Any]], map_id: str) -> str:
    if not points:
        return c.empty_state("No map points")

    heat_points: list[list[float]] = []
    by_place: dict[str, dict[str, Any]] = {}
    for point in points:
        place = str(point["place_name"])
        lat = float(point["lat"])
        lon = float(point["lon"])
        heat_points.append([lat, lon, 1])
        bucket = by_place.setdefault(
            place,
            {"place": place, "count": 0, "lat_sum": 0.0, "lon_sum": 0.0, "last_seen": ""},
        )
        bucket["count"] += 1
        bucket["lat_sum"] += lat
        bucket["lon_sum"] += lon
        bucket["last_seen"] = max(str(bucket["last_seen"]), str(point.get("ts") or ""))
    markers = []
    for bucket in sorted(by_place.values(), key=lambda item: int(item["count"]), reverse=True)[:16]:
        count = int(bucket["count"])
        markers.append(
            {
                "place": bucket["place"],
                "count": count,
                "lat": float(bucket["lat_sum"]) / count,
                "lon": float(bucket["lon_sum"]) / count,
                "last_seen": _time(bucket["last_seen"]),
            }
        )
    points_json = _js_json(heat_points)
    markers_json = _js_json(markers)
    safe_map_id = html.escape(map_id, quote=True)
    map_id_json = _js_json(map_id)
    loader = _leaflet_loader("initPlacesHeatMap", needs_heat=True)
    return f"""
<p class="meta">{len(points):,} GPS points · OpenStreetMap tiles</p>
<div id="{safe_map_id}" class="places-leaflet-map places-map"></div>
<script>
function initPlacesHeatMap() {{
  var mapId = {map_id_json};
  var el = document.getElementById(mapId);
  if (!el || el.dataset.initialized === '1') return;
  el.dataset.initialized = '1';
  var pts = {points_json};
  var markers = {markers_json};
  var map = L.map(mapId);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19
  }}).addTo(map);
  L.heatLayer(pts, {{radius: 18, blur: 22, maxZoom: 14}}).addTo(map);
  function esc(value) {{
    return String(value).replace(/[&<>"']/g, function(ch) {{
      return ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[ch];
    }});
  }}
  markers.forEach(function(m) {{
    L.circleMarker([m.lat, m.lon], {{
      radius: Math.min(18, 6 + Math.sqrt(m.count)),
      fillColor: '#f59e0b',
      fillOpacity: 0.72,
      color: '#000',
      weight: 1
    }}).addTo(map).bindPopup(
      '<strong>' + esc(m.place) + '</strong><br>' + m.count + ' points<br>last ' + esc(m.last_seen)
    );
  }});
  var bounds = L.latLngBounds(pts.map(function(p) {{ return [p[0], p[1]]; }}));
  map.fitBounds(bounds, {{padding: [24, 24]}});
}}
</script>
{loader}
"""


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _detect_stops(
    locs: list[dict[str, Any]],
    *,
    radius_m: float = _STOP_RADIUS_M,
    min_minutes: float = _STOP_MIN_MINUTES,
) -> list[dict[str, Any]]:
    if len(locs) < 2:
        return []
    sorted_locs = sorted(locs, key=lambda x: x["dt"])
    used: set[int] = set()
    clusters: list[list[int]] = []
    for i in range(len(sorted_locs)):
        if i in used:
            continue
        cluster = [i]
        used.add(i)
        clat, clon = sorted_locs[i]["lat"], sorted_locs[i]["lon"]
        count = 1
        for j in range(len(sorted_locs)):
            if j in used:
                continue
            d = _haversine_m(clat, clon, sorted_locs[j]["lat"], sorted_locs[j]["lon"])
            if d < radius_m:
                cluster.append(j)
                used.add(j)
                clat = (clat * count + sorted_locs[j]["lat"]) / (count + 1)
                clon = (clon * count + sorted_locs[j]["lon"]) / (count + 1)
                count += 1
        clusters.append(cluster)

    stops: list[dict[str, Any]] = []
    for cluster in clusters:
        pts = [sorted_locs[idx] for idx in sorted(cluster, key=lambda idx: sorted_locs[idx]["dt"])]
        total_min = 0.0
        for k in range(1, len(pts)):
            gap = (pts[k]["dt"] - pts[k - 1]["dt"]).total_seconds() / 60.0
            if gap <= 30:
                total_min += gap
        if total_min < min_minutes:
            continue
        avg_lat = sum(float(p["lat"]) for p in pts) / len(pts)
        avg_lon = sum(float(p["lon"]) for p in pts) / len(pts)
        stops.append(
            {
                "lat": avg_lat,
                "lon": avg_lon,
                "start": pts[0]["dt"].isoformat(),
                "end": pts[-1]["dt"].isoformat(),
                "minutes": round(total_min, 1),
                "count": len(pts),
            }
        )
    return stops


def _movement_map_html(points: list[dict[str, Any]], map_id: str) -> str:
    locs: list[dict[str, Any]] = []
    for point in points:
        accuracy = point.get("accuracy")
        if accuracy not in (None, ""):
            try:
                if float(accuracy) > _ACCURACY_M_MAX:
                    continue
            except (TypeError, ValueError):
                pass
        parsed = _parse_datetime(point.get("ts"))
        if parsed is None:
            continue
        locs.append(
            {
                "dt": parsed,
                "lat": float(point["lat"]),
                "lon": float(point["lon"]),
                "place": str(point.get("place_name") or "(unlabeled)"),
            }
        )
    if not locs:
        return c.empty_state(f"No high-accuracy GPS points in the last 24h (<= {_ACCURACY_M_MAX}m)")

    stops = _detect_stops(locs)
    for stop in stops:
        best_dist = float("inf")
        best_place = None
        for loc in locs:
            if loc.get("place") == "(unlabeled)":
                continue
            dist = _haversine_m(stop["lat"], stop["lon"], loc["lat"], loc["lon"])
            if dist < best_dist:
                best_dist = dist
                best_place = loc["place"]
        stop["place"] = best_place
    payload = {
        "path": [[loc["lat"], loc["lon"]] for loc in locs],
        "stops": stops,
        "latest": {"lat": locs[-1]["lat"], "lon": locs[-1]["lon"], "ts": locs[-1]["dt"].isoformat()},
    }
    payload_json = _js_json(payload)
    safe_map_id = html.escape(map_id, quote=True)
    map_id_json = _js_json(map_id)
    loader = _leaflet_loader("initPlacesMovementMap")
    return f"""
<p class="meta">{len(locs):,} GPS points · {len(stops):,} stops · last 24h · OpenStreetMap tiles</p>
<div id="{safe_map_id}" class="places-leaflet-map places-map"></div>
<script>
function initPlacesMovementMap() {{
  var mapId = {map_id_json};
  var el = document.getElementById(mapId);
  if (!el || el.dataset.initialized === '1') return;
  el.dataset.initialized = '1';
  var data = {payload_json};
  var map = L.map(mapId);
  L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19
  }}).addTo(map);
  if (data.path.length > 1) {{
    L.polyline(data.path, {{
      color: '#2563eb',
      weight: 4,
      opacity: 0.72,
      lineCap: 'round',
      lineJoin: 'round'
    }}).addTo(map);
  }}
  function fmtDur(min) {{
    if (min >= 60) return Math.floor(min / 60) + 'h ' + Math.round(min % 60) + 'm';
    return Math.round(min) + 'm';
  }}
  function fmtTime(iso) {{
    return new Date(iso).toLocaleTimeString([], {{hour: '2-digit', minute: '2-digit'}});
  }}
  function esc(value) {{
    return String(value).replace(/[&<>"']/g, function(ch) {{
      return ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[ch];
    }});
  }}
  data.stops.forEach(function(s) {{
    var label = (s.place ? '<strong>' + esc(s.place) + '</strong><br>' : '<strong>unknown place</strong><br>')
      + fmtDur(s.minutes) + ' · ' + fmtTime(s.start) + ' - ' + fmtTime(s.end)
      + ' · ' + s.count + ' pts';
    L.circleMarker([s.lat, s.lon], {{
      radius: Math.min(20, 8 + s.minutes / 5),
      fillColor: '#ef4444',
      fillOpacity: 0.62,
      color: '#ef4444',
      weight: 2
    }}).addTo(map).bindPopup(label);
  }});
  L.circleMarker([data.latest.lat, data.latest.lon], {{
    radius: 9,
    fillColor: '#2563eb',
    fillOpacity: 1,
    color: 'white',
    weight: 3
  }}).addTo(map).bindPopup('Latest · ' + new Date(data.latest.ts).toLocaleString());
  map.fitBounds(L.latLngBounds(data.path), {{padding: [30, 30]}});
}}
</script>
{loader}
"""


def _daily_series(ctx: AppContext, aliases: dict[str, tuple[str, bool]], days: int) -> list[dict[str, Any]]:
    if _table_exists(ctx, "daily_locations"):
        rows = _q(ctx, "daily_locations_recent", cutoff=_cutoff(days)[:10], limit=days * 12)
    else:
        return _timeline_rows(ctx, aliases, days)
    out: list[dict[str, Any]] = []
    for row in rows:
        place = str(row.get("place_name") or "(unlabeled)")
        display = _display_place(place, aliases)
        if not display:
            continue
        item = dict(row)
        item["place_name"] = display
        out.append(item)
    return out


def _timeline_rows(ctx: AppContext, aliases: dict[str, tuple[str, bool]], days: int) -> list[dict[str, Any]]:
    location_source = _location_source(ctx)
    geocode_source = _geocode_source(ctx)
    if location_source == "location_points" and geocode_source == "recorded_at":
        rows = _run(
            ctx,
            """
            SELECT date(p.recorded_at, 'localtime') AS day,
                   coalesce(g.formatted_address, '(unlabeled)') AS place_name,
                   min(p.recorded_at) AS arrived_at,
                   max(p.recorded_at) AS left_at,
                   count(*) AS points
            FROM location_points p
            LEFT JOIN geocoded_locations g ON g.recorded_at = p.recorded_at
            WHERE p.recorded_at >= ?
            GROUP BY day, coalesce(g.place_id, g.formatted_address, '(unlabeled)')
            ORDER BY day DESC, arrived_at DESC
            LIMIT ?
            """,
            (_cutoff(days), 500),
        )
    elif location_source == "location_points":
        rows = _run(
            ctx,
            """
            SELECT date(recorded_at, 'localtime') AS day,
                   '(unlabeled)' AS place_name,
                   min(recorded_at) AS arrived_at,
                   max(recorded_at) AS left_at,
                   count(*) AS points
            FROM location_points
            WHERE recorded_at >= ?
            GROUP BY day
            ORDER BY day DESC, arrived_at DESC
            LIMIT ?
            """,
            (_cutoff(days), 500),
        )
    elif location_source == "raw_locations" and geocode_source == "source_id":
        rows = _q(ctx, "timeline_groups", cutoff=_cutoff(days), limit=500)
    else:
        rows = _run(
            ctx,
            """
            SELECT date(ts, 'localtime') AS day,
                   '(unlabeled)' AS place_name,
                   min(ts) AS arrived_at,
                   max(ts) AS left_at,
                   count(*) AS points
            FROM raw_locations
            WHERE ts >= ?
            GROUP BY day
            ORDER BY day DESC, arrived_at DESC
            LIMIT ?
            """,
            (_cutoff(days), 500),
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        place = str(row.get("place_name") or "(unlabeled)")
        display = _display_place(place, aliases)
        if not display:
            continue
        item = dict(row)
        item["place_name"] = display
        item["time"] = f"{_short_time(row.get('arrived_at'))}-{_short_time(row.get('left_at'))}"
        item["duration"] = _duration(row.get("arrived_at"), row.get("left_at"))
        out.append(item)
    return out


def render_overview(ctx: AppContext) -> str:
    settings = _settings(ctx)
    aliases = _alias_state(ctx)
    days = _int_setting(settings, "default_days", 30)
    points = _raw_points(ctx, aliases, days=days, limit=900)
    top = _top_places(ctx, aliases, days=days, limit=10)
    top_items = [(row["place_name"], float(row["points"] or 0)) for row in top]
    daily = _daily_series(ctx, aliases, days=days)
    chart_rows = [
        {
            "date": row.get("date") or row.get("day"),
            "place": row["place_name"],
            "visits": int(row.get("visits") or row.get("points") or 0),
        }
        for row in daily[:120]
    ]
    daily_chart = c.chart(
        {
            "data": list(reversed(chart_rows)),
            "series": [
                {"type": "bar", "xKey": "date", "yKey": "visits", "yName": "Visits", "fill": "#2563eb"}
            ],
            "axes": {"bottom": {"type": "category"}, "left": {"type": "number"}},
            "legend": {"enabled": False},
        },
        height_px=220,
    )
    body = c.section(
        "Map",
        _map_html(points, "places-overview-map"),
        subtitle=f"Past {days} days, rendered with Leaflet and OpenStreetMap.",
    )
    if top_items:
        body += c.section("Frequent Places", horizontal_bars(top_items, value_fmt=lambda v: f"{int(v):,}"))
    return c.page(
        "Places",
        _style(),
        _map_notice(),
        c.metric_grid(_metrics(ctx, aliases)),
        '<div class="places-two-col">'
        f'<div>{body}</div>'
        f'<div>{c.section("Daily Signal", daily_chart)}</div>'
        "</div>",
        subtitle="Local location timeline and rhythm analysis from exported mobile location history.",
        nav=_nav(ctx, "overview"),
    )


def render_map(ctx: AppContext) -> str:
    settings = _settings(ctx)
    aliases = _alias_state(ctx)
    days = _int_setting(settings, "default_days", 30)
    points = _raw_points(ctx, aliases, days=days, limit=2000)
    movement_points = _raw_points(ctx, aliases, days=1, limit=2500)
    top = _top_places(ctx, aliases, days=days, limit=40)
    rows = [
        {
            "place": row["place_name"],
            "points": row["points"],
            "days": row["days"],
            "last_seen": _time(row["last_seen"]),
        }
        for row in top
    ]
    return c.page(
        "Map",
        _style(),
        _map_notice(),
        c.section(
            "Location Heatmap",
            _map_html(points, "places-main-map"),
            subtitle=f"Past {days} days, matching the original location visualization approach.",
        ),
        c.section("Movement (24h)", _movement_map_html(movement_points, "places-movement-map")),
        c.section("Map Labels", c.data_grid(rows, ["place", "points", "days", "last_seen"], height_px=420)),
        subtitle="Heatmap, recent movement, and geocoded place labels from local location history.",
        nav=_nav(ctx, "map"),
    )


def render_timeline(ctx: AppContext) -> str:
    settings = _settings(ctx)
    aliases = _alias_state(ctx)
    days = _int_setting(settings, "default_days", 30)
    rows = _timeline_rows(ctx, aliases, days)
    table_rows = [
        {
            "day": row["day"],
            "time": row["time"],
            "duration": row["duration"],
            "place": row["place_name"],
            "points": row["points"],
        }
        for row in rows
    ]
    return c.page(
        "Timeline",
        _style(),
        c.section(
            "Recent Place Timeline",
            c.data_grid(table_rows, ["day", "time", "duration", "place", "points"], page_size=30, height_px=680),
            subtitle=f"Grouped from raw points over the past {days} days.",
        ),
        subtitle="A place-first audit trail for where days actually went.",
        nav=_nav(ctx, "timeline"),
    )


def render_rhythm(ctx: AppContext) -> str:
    settings = _settings(ctx)
    aliases = _alias_state(ctx)
    days = _int_setting(settings, "default_days", 30)
    source = _location_source(ctx)
    if source == "location_points":
        hourly_rows = _run(
            ctx,
            """
            SELECT cast(strftime('%w', recorded_at, 'localtime') AS INTEGER) AS weekday,
                   cast(strftime('%H', recorded_at, 'localtime') AS INTEGER) AS hour,
                   count(*) AS points
            FROM location_points
            WHERE recorded_at >= ?
            GROUP BY weekday, hour
            ORDER BY weekday, hour
            """,
            (_cutoff(days),),
        )
    else:
        hourly_rows = _q(ctx, "hourly_rhythm", cutoff=_cutoff(days))
    by_weekday_hour = {
        (int(row["weekday"]), int(row["hour"])): int(row["points"] or 0) for row in hourly_rows
    }
    grid = [[by_weekday_hour.get((weekday, hour), 0) or None for hour in range(24)] for weekday in range(7)]
    top = _top_places(ctx, aliases, days=days, limit=12)
    rhythm_chart = heatmap(
        grid,
        _WEEKDAYS,
        [f"{hour:02d}" for hour in range(24)],
        base_color=(20, 184, 166),
    )
    place_data = [
        {
            "place": row["place_name"],
            "points": int(row["points"] or 0),
            "days": int(row["days"] or 0),
            "first": _time(row["first_seen"])[:10],
            "last": _time(row["last_seen"])[:10],
        }
        for row in top
    ]
    scatter = agcharts.chart(
        {
            "data": place_data,
            "series": [
                {
                    "type": "scatter",
                    "xKey": "days",
                    "yKey": "points",
                    "labelKey": "place",
                    "marker": {"fill": "#f59e0b", "stroke": "#000"},
                }
            ],
            "axes": {"bottom": {"type": "number", "title": {"text": "days"}}, "left": {"type": "number", "title": {"text": "points"}}},
            "legend": {"enabled": False},
        },
        height_px=280,
    )
    return c.page(
        "Rhythm",
        _style(),
        c.section("Week By Hour", rhythm_chart, subtitle=f"Point density over the past {days} days."),
        c.section("Place Regularity", scatter),
        c.section("Regular Places", c.data_grid(place_data, ["place", "points", "days", "first", "last"], height_px=380)),
        subtitle="Life rhythm from when and where location points cluster.",
        nav=_nav(ctx, "rhythm"),
    )


def render_privacy(ctx: AppContext) -> str:
    settings = _settings(ctx)
    aliases = _q(ctx, "aliases")
    days = html.escape(settings.get("default_days", "30"))
    settings_form = (
        f'<form class="places-settings-form" method="post" action="{ctx.action_url("set_privacy")}">'
        '<input type="hidden" name="blur_precision_m" value="0">'
        '<input type="hidden" name="hide_coordinates" value="0">'
        f'<label>default days<input name="default_days" value="{days}" inputmode="numeric"></label>'
        '<button type="submit">save</button>'
        "</form>"
    )
    alias_form = (
        f'<form class="places-alias-form" method="post" action="{ctx.action_url("set_place_alias")}">'
        '<label>source place<input name="place_name" placeholder="Home address label"></label>'
        '<label>alias<input name="alias" placeholder="Home"></label>'
        '<label class="places-checkbox"><input type="checkbox" name="hidden" value="1"> hidden</label>'
        '<button type="submit">save alias</button>'
        "</form>"
    )
    alias_rows = [
        {
            "place_name": row["place_name"],
            "alias": row["alias"],
            "hidden": "yes" if row["hidden"] else "no",
            "updated_at": _time(row["updated_at"]),
        }
        for row in aliases
    ]
    return c.page(
        "Settings",
        _style(),
        c.section(
            "Display Settings",
            settings_form,
            subtitle="App settings only; source location rows are unchanged.",
        ),
        c.section("Place Aliases", alias_form),
        c.section("Saved Aliases", c.data_grid(alias_rows, ["place_name", "alias", "hidden", "updated_at"], height_px=420)),
        subtitle="Local presentation controls for range and place labels.",
        nav=_nav(ctx, "privacy"),
    )
