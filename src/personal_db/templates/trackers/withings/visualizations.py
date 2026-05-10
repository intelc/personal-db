"""Visualizations for the withings tracker."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import line_chart, multi_line_chart


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


# kg ↔ lb toggle. Charts emit raw kg in `data-kg` and unit text in
# `<span data-kg-unit>`. The script (init-once via window guard) flips both
# in place when the user clicks kg/lb. Persists choice in localStorage.
_UNIT_TOGGLE_HTML = (
    '<div class="unit-toggle" data-unit-toggle>'
    '<button type="button" data-unit="kg">kg</button>'
    '<button type="button" data-unit="lb">lb</button>'
    "</div>"
)

_UNIT_TOGGLE_JS = """\
<script>
(function(){
  if (window.__pdbUnitToggleInit) return;
  window.__pdbUnitToggleInit = true;
  var KG_TO_LB = 2.20462262;
  function fmt(n){ return (Math.round(n * 100) / 100).toString(); }
  function setUnit(u){
    try { localStorage.setItem('pdb_unit', u); } catch(e) {}
    document.querySelectorAll('[data-unit-toggle] button[data-unit]').forEach(function(b){
      b.classList.toggle('active', b.dataset.unit === u);
    });
    document.querySelectorAll('[data-kg]').forEach(function(el){
      var kg = parseFloat(el.dataset.kg);
      if (!isFinite(kg)) return;
      el.textContent = u === 'lb' ? fmt(kg * KG_TO_LB) : fmt(kg);
    });
    document.querySelectorAll('[data-kg-unit]').forEach(function(el){
      el.textContent = u;
    });
  }
  document.addEventListener('click', function(e){
    var btn = e.target.closest && e.target.closest('[data-unit-toggle] button[data-unit]');
    if (btn) setUnit(btn.dataset.unit);
  });
  var saved = null;
  try { saved = localStorage.getItem('pdb_unit'); } catch(e) {}
  setUnit(saved === 'lb' ? 'lb' : 'kg');
})();
</script>
"""


def render_weight_trend_180d(cfg: Config) -> str:
    """Daily weight (kg) over the last 180 days. Manual entries excluded.

    If there are multiple weigh-ins in a day, the latest one wins."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = datetime.now().date()
    cutoff = (today - timedelta(days=179)).isoformat()
    try:
        rows = dict(con.execute(
            "SELECT date(date) AS d, weight_kg "
            "FROM withings_measurements "
            "WHERE date >= ? AND weight_kg IS NOT NULL "
            "  AND attrib NOT IN (2, 4) "
            "GROUP BY d "
            "HAVING date = MAX(date)",
            (cutoff,),
        ).fetchall())
    except sqlite3.OperationalError:
        return '<p class="meta">withings_measurements not synced yet</p>'
    finally:
        con.close()

    items: list[tuple[str, float | None]] = []
    for i in range(179, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d)))

    return (
        _UNIT_TOGGLE_HTML
        + '<p class="meta">withings weight (<span data-kg-unit>kg</span>) · '
        "last 180 days · device measurements only</p>"
        + line_chart(
            items,
            color="#3a6ea8",
            show_every_nth_label=30,
            value_attr="data-kg",
        )
        + _UNIT_TOGGLE_JS
    )


def render_body_composition_30d(cfg: Config) -> str:
    """Last 30 days. Lines for fat_mass_kg and lean_mass_kg.

    The two together account for total body weight on most Withings scales,
    so the visual answers 'is recent weight change fat or lean?'."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = datetime.now().date()
    cutoff = (today - timedelta(days=29)).isoformat()
    try:
        rows = con.execute(
            "SELECT date(date) AS d, "
            "       MAX(fat_mass_kg)  AS fat, "
            "       MAX(lean_mass_kg) AS lean "
            "FROM withings_measurements "
            "WHERE date >= ? AND attrib NOT IN (2, 4) "
            "GROUP BY d",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">withings_measurements not synced yet</p>'
    finally:
        con.close()
    by_day = {row[0]: (row[1], row[2]) for row in rows}

    x_labels: list[str] = []
    fat_vals: list[float | None] = []
    lean_vals: list[float | None] = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        fat, lean = by_day.get(d, (None, None))
        x_labels.append(d[5:])
        fat_vals.append(fat)
        lean_vals.append(lean)

    return (
        _UNIT_TOGGLE_HTML
        + '<p class="meta">withings body composition (<span data-kg-unit>kg</span>) · '
        "last 30 days</p>"
        + multi_line_chart(
            x_labels,
            series=[
                ("fat mass", fat_vals, "#cc6644"),
                ("lean mass", lean_vals, "#3a8a4a"),
            ],
            show_every_nth_label=5,
            value_attr="data-kg",
        )
        + _UNIT_TOGGLE_JS
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "weight_trend_180d",
            "name": "Weight Trend (180d)",
            "description": "Daily weight in kilograms over the last 180 days, device measurements only.",
            "render": render_weight_trend_180d,
        },
        {
            "slug": "body_composition_30d",
            "name": "Body Composition (30d)",
            "description": "Fat mass vs lean mass, day by day, over the last 30 days.",
            "render": render_body_composition_30d,
        },
    ]
