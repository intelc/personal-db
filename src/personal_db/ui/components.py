"""Stable HTML components for personal_db apps."""

from __future__ import annotations

from html import escape
from typing import Any

from personal_db.ui import agcharts
from personal_db.ui.aggrid import grid as ag_grid
from personal_db.ui.aggrid import table_grid


class SafeHtml(str):
    pass


def join_html(parts: list[str]) -> str:
    return "".join(str(part) for part in parts if part)


def page(
    title: str,
    *children: str,
    subtitle: str = "",
    header_extra: str = "",
    nav: list[tuple[str, str, bool] | tuple[str, str, bool, dict[str, str]]] | None = None,
) -> str:
    """Render a complete app page.

    App route templates intentionally do not render their own app title/tabs;
    app views should return this helper once so page chrome is not duplicated.
    """
    nav_html = ""
    if nav:
        links = ""
        for item in nav:
            label, href, active = item[:3]
            attrs = item[3] if len(item) > 3 else {}
            active_attr = ' class="active"' if active else ""
            extra_attrs = "".join(
                f' {escape(str(key), quote=True)}="{escape(str(value), quote=True)}"'
                for key, value in attrs.items()
            )
            links += f'<a href="{escape(href)}"{active_attr}{extra_attrs}>{escape(label)}</a>'
        nav_html = f'<nav class="app-tabs">{links}</nav>'
    subtitle_html = f'<p class="meta">{escape(subtitle)}</p>' if subtitle else ""
    return (
        '<article class="app-page">'
        f'<header class="app-page-header"><div><h1>{escape(title)}</h1>{subtitle_html}</div></header>'
        f"{header_extra}"
        f"{nav_html}"
        f'{join_html(list(children))}'
        "</article>"
    )


def section(title: str, *children: str, subtitle: str = "", class_name: str = "") -> str:
    klass = "app-section"
    if class_name:
        klass += f" {escape(class_name)}"
    subtitle_html = f'<p class="meta">{escape(subtitle)}</p>' if subtitle else ""
    return f'<section class="{klass}"><h2>{escape(title)}</h2>{subtitle_html}{join_html(list(children))}</section>'


def metric_card(label: str, value: str, *, hint: str = "") -> str:
    hint_html = f'<span class="metric-hint">{escape(hint)}</span>' if hint else ""
    return (
        '<div class="metric-card">'
        f'<span class="metric-label">{escape(label)}</span>'
        f'<strong>{escape(value)}</strong>'
        f"{hint_html}</div>"
    )


def metric_grid(items: list[tuple[str, str] | tuple[str, str, str]]) -> str:
    cards = []
    for item in items:
        label, value = item[0], item[1]
        hint = item[2] if len(item) > 2 else ""
        cards.append(metric_card(label, value, hint=hint))
    return f'<div class="metric-grid">{join_html(cards)}</div>'


def data_grid(
    rows: list[dict[str, Any]] | list[tuple],
    columns: list[str] | list[dict[str, Any]],
    *,
    class_name: str = "",
    page_size: int = 15,
    height_px: int | None = None,
    html_columns: set[int] | None = None,
) -> str:
    if not rows:
        return empty_state("No data")
    if rows and isinstance(rows[0], dict):
        normalized_columns: list[dict[str, Any]] = []
        html_columns = html_columns or set()
        for idx, col in enumerate(columns):
            if isinstance(col, str):
                normalized_col = {
                    "field": col,
                    "headerName": col.replace("_", " ").title(),
                }
            else:
                normalized_col = dict(col)
            if idx in html_columns:
                normalized_col["cellRenderer"] = "html"
            normalized_columns.append(normalized_col)
        return ag_grid(
            normalized_columns,
            rows,  # type: ignore[arg-type]
            class_name=class_name,
            page_size=page_size,
            height_px=height_px,
        )
    return table_grid(
        rows,  # type: ignore[arg-type]
        columns,  # type: ignore[arg-type]
        class_name=class_name,
        page_size=page_size,
        height_px=height_px,
        html_columns=html_columns,
    )


def chart(options: dict[str, Any], *, height_px: int = 220, class_name: str = "") -> str:
    return agcharts.chart(options, height_px=height_px, class_name=class_name)


def tabs(items: list[tuple[str, str, bool]]) -> str:
    links = ""
    for label, href, active in items:
        active_attr = ' class="active"' if active else ""
        links += f'<a href="{escape(href)}"{active_attr}>{escape(label)}</a>'
    return f'<nav class="app-tabs">{links}</nav>'


def action_button(label: str, url: str, *, method: str = "post") -> str:
    return (
        f'<form class="inline-action" method="{escape(method)}" action="{escape(url)}">'
        f'<button type="submit">{escape(label)}</button></form>'
    )


def notice(message: str, *, kind: str = "info") -> str:
    return f'<p class="notice notice-{escape(kind)}">{escape(message)}</p>'


def empty_state(message: str) -> str:
    return f'<p class="meta empty-state">{escape(message)}</p>'
