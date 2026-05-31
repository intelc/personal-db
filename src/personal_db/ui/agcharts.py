"""Small AG Charts Community HTML bridge for tracker visualizations."""

from __future__ import annotations

import json
from html import escape
from itertools import count
from typing import Any

_IDS = count(1)


def _payload(options: dict[str, Any]) -> str:
    return json.dumps(options, ensure_ascii=False).replace("</", "<\\/")


def chart(
    options: dict[str, Any],
    *,
    height_px: int = 220,
    class_name: str = "",
) -> str:
    chart_id = f"pdb-chart-{next(_IDS)}"
    classes = "pdb-chart"
    if class_name:
        classes += f" {escape(class_name)}"
    return (
        f'<div class="pdb-chart-wrap">'
        f'<div id="{chart_id}" class="{classes}" data-pdb-chart '
        f'style="height:{height_px}px"></div>'
        f'<script type="application/json" data-pdb-chart-options="{chart_id}">'
        f"{_payload(options)}</script></div>"
    )


def line_chart(
    items: list[tuple[str, float | None]],
    *,
    color: str = "#000",
    height_px: int = 180,
    y_min: float | None = None,
    y_max: float | None = None,
    value_attr: str | None = None,
    value_format: str | None = None,
    zoom: bool = True,
    focus_scale: bool = True,
    legend_position: str | None = None,
    month_markers: bool = False,
    **_ignored: Any,
) -> str:
    if value_format is None and value_attr == "data-usd":
        value_format = "usd"
    return multi_line_chart(
        [label for label, _ in items],
        [("", [value for _, value in items], color)],
        height_px=height_px,
        y_min=y_min,
        y_max=y_max,
        value_attr=value_attr,
        value_format=value_format,
        zoom=zoom,
        focus_scale=focus_scale,
        legend_position=legend_position,
        month_markers=month_markers,
    )


def multi_line_chart(
    x_labels: list[str],
    series: list,
    *,
    height_px: int = 220,
    y_min: float | None = None,
    y_max: float | None = None,
    value_attr: str | None = None,
    value_format: str | None = None,
    zoom: bool = True,
    zoom_default_window: int | None = None,
    focus_scale: bool = True,
    legend_position: str | None = None,
    month_markers: bool = False,
    **_ignored: Any,
) -> str:
    if not x_labels or not series:
        return '<p class="meta">no data</p>'
    if value_format is None and value_attr == "data-usd":
        value_format = "usd"

    data = [{"x": label} for label in x_labels]
    chart_series: list[dict[str, Any]] = []
    for i, raw in enumerate(series):
        name, values, color = raw[0], raw[1], raw[2]
        style = raw[3] if len(raw) > 3 and raw[3] else {}
        key = f"y{i}"
        has_values = False
        for row, value in zip(data, values, strict=False):
            if value is not None:
                row[key] = value
                has_values = True
        if not has_values:
            continue
        entry: dict[str, Any] = {
            "type": "line",
            "xKey": "x",
            "yKey": key,
            "yName": name or key,
            "stroke": color,
            "marker": {"enabled": bool(style.get("dots", True)), "fill": color, "stroke": color},
            "strokeWidth": float(style.get("width", 2)),
        }
        if style.get("dash"):
            entry["lineDash"] = [float(x) for x in str(style["dash"]).split(",") if x]
        chart_series.append(entry)

    if not chart_series:
        return '<p class="meta">no data</p>'

    axes: dict[str, dict[str, Any]] = {
        "bottom": {"type": "category"},
        "left": {"type": "number"},
    }
    if y_min is not None:
        axes["left"]["min"] = y_min
    if y_max is not None:
        axes["left"]["max"] = y_max

    legend: dict[str, Any] = {"enabled": any(s.get("yName") != s.get("yKey") for s in chart_series)}
    if legend_position:
        legend["position"] = legend_position

    options: dict[str, Any] = {
        "data": data,
        "series": chart_series,
        "axes": axes,
        "legend": legend,
    }
    if zoom:
        options["pdbZoom"] = {
            "enabled": True,
            "windows": [365, 180, 90, 30, 7],
        }
        if zoom_default_window:
            options["pdbZoom"]["defaultWindow"] = int(zoom_default_window)
    if focus_scale and y_min is None and y_max is None:
        options["pdbScale"] = {
            "enabled": True,
            "mode": "auto",
            "lowerQuantile": 0.05,
            "upperQuantile": 0.95,
        }
    if month_markers:
        options["pdbTimeMarkers"] = {
            "enabled": True,
            "monthBoundaries": True,
            "xKey": "x",
        }
    if value_format:
        options["valueFormat"] = value_format
    return chart(options, height_px=height_px, class_name="pdb-line-chart")


def gain_loss_area_chart(
    x_labels: list[str],
    values: list[float | None],
    *,
    date_values: list[str] | None = None,
    aggregation: bool = False,
    aggregation_default_mode: str = "day",
    scale_default_mode: str | None = None,
    height_px: int = 220,
    y_min: float | None = None,
    y_max: float | None = None,
    value_attr: str | None = None,
    value_format: str | None = None,
    zoom: bool = True,
    zoom_default_window: int | None = None,
    focus_scale: bool = True,
    month_markers: bool = False,
    positive_color: str = "#167a3f",
    negative_color: str = "#b23a48",
    line_color: str = "#111111",
    extra_values: dict[str, list[Any]] | None = None,
    tooltip_fields: list[dict[str, str]] | None = None,
    aggregation_sum_keys: list[str] | None = None,
    **_ignored: Any,
) -> str:
    if not x_labels or not values:
        return '<p class="meta">no data</p>'
    if value_format is None and value_attr == "data-usd":
        value_format = "usd"

    data: list[dict[str, Any]] = []
    has_values = False
    for index, (label, raw_value) in enumerate(zip(x_labels, values, strict=False)):
        row: dict[str, Any] = {"x": label}
        if date_values and index < len(date_values):
            row["date"] = date_values[index]
        for key, extra in (extra_values or {}).items():
            if index < len(extra):
                row[key] = extra[index]
        if raw_value is not None:
            value = float(raw_value)
            row["net"] = value
            row["gain"] = value if value > 0 else 0
            row["loss"] = value if value < 0 else 0
            has_values = True
        data.append(row)
    if not has_values:
        return '<p class="meta">no data</p>'

    axes: dict[str, dict[str, Any]] = {
        "bottom": {"type": "category"},
        "left": {"type": "number"},
    }
    if y_min is not None:
        axes["left"]["min"] = y_min
    if y_max is not None:
        axes["left"]["max"] = y_max

    options: dict[str, Any] = {
        "data": data,
        "series": [
            {
                "type": "area",
                "xKey": "x",
                "yKey": "gain",
                "yName": "Net gain",
                "stroke": positive_color,
                "fill": positive_color,
                "fillOpacity": 0.18,
                "strokeWidth": 0,
                "marker": {"enabled": False},
                "tooltip": {"enabled": False},
                "stacked": False,
            },
            {
                "type": "area",
                "xKey": "x",
                "yKey": "loss",
                "yName": "Net loss",
                "stroke": negative_color,
                "fill": negative_color,
                "fillOpacity": 0.18,
                "strokeWidth": 0,
                "marker": {"enabled": False},
                "tooltip": {"enabled": False},
                "stacked": False,
            },
            {
                "type": "line",
                "xKey": "x",
                "yKey": "net",
                "yName": "Net",
                "stroke": line_color,
                "marker": {"enabled": True, "fill": line_color, "stroke": line_color},
                "strokeWidth": 2.2,
            },
        ],
        "axes": axes,
        "legend": {"enabled": False},
    }
    if zoom:
        options["pdbZoom"] = {
            "enabled": True,
            "windows": [365, 180, 90, 30, 7],
        }
        if zoom_default_window:
            options["pdbZoom"]["defaultWindow"] = int(zoom_default_window)
    if focus_scale and y_min is None and y_max is None:
        options["pdbScale"] = {
            "enabled": True,
            "mode": "auto",
            "lowerQuantile": 0.05,
            "upperQuantile": 0.95,
        }
        if scale_default_mode in {"full", "focus"}:
            options["pdbScale"]["defaultMode"] = scale_default_mode
    if month_markers:
        options["pdbTimeMarkers"] = {
            "enabled": True,
            "monthBoundaries": True,
            "xKey": "x",
        }
        if date_values:
            options["pdbTimeMarkers"]["dateKey"] = "date"
    if aggregation and date_values:
        options["pdbAggregation"] = {
            "enabled": True,
            "dateKey": "date",
            "modes": ["day", "week", "month"],
            "defaultMode": aggregation_default_mode
            if aggregation_default_mode in {"day", "week", "month"}
            else "day",
            "sumKeys": aggregation_sum_keys or ["net"],
            "deriveGainLoss": True,
        }
    if tooltip_fields:
        options["pdbTooltip"] = {"fields": tooltip_fields}
    if value_format:
        options["valueFormat"] = value_format
    return chart(options, height_px=height_px, class_name="pdb-line-chart pdb-net-area-chart")


def pie_chart(
    items: list[tuple[str, float]],
    *,
    height_px: int = 260,
    value_format: str | None = None,
) -> str:
    data = [
        {"label": str(label or "Unknown"), "value": float(value or 0)}
        for label, value in items
        if float(value or 0) > 0
    ]
    if not data:
        return '<p class="meta">no holdings to chart</p>'
    options: dict[str, Any] = {
        "data": data,
        "series": [
            {
                "type": "pie",
                "angleKey": "value",
                "calloutLabelKey": "label",
                "sectorLabelKey": "value",
            }
        ],
        "legend": {"enabled": True},
    }
    if value_format:
        options["valueFormat"] = value_format
    return chart(options, height_px=height_px, class_name="pdb-pie-chart")
