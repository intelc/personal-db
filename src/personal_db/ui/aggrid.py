"""Small AG Grid Community HTML bridge for tracker visualizations.

Tracker code should pass structured columns/rows here instead of emitting
table markup or JavaScript. The browser initializer in static/pdb-grid.js owns
the actual AG Grid setup and maps a tiny allow-list of renderer names.
"""

from __future__ import annotations

import json
from html import escape
from itertools import count
from typing import Any

_IDS = count(1)


def _field_name(i: int) -> str:
    return f"c{i}"


def table_grid(
    rows: list[tuple],
    headers: list[str],
    *,
    class_name: str = "",
    page_size: int = 15,
    html_columns: set[int] | None = None,
    group_index: int | None = None,
    group_label: str = "Group",
    item_label: str = "rows",
    height_px: int | None = None,
) -> str:
    """Render tuple rows as an AG Grid table.

    `html_columns` is for internally-generated, already-sanitized snippets
    such as badges. Plain cell values remain text-only in the grid.
    `group_index` keeps the grouped finance-table affordance without using
    Enterprise row grouping: the group value is rendered as a pinned-looking
    full-width row inserted into the client-side data.
    """
    if not rows:
        return '<p class="meta">no data</p>'

    html_columns = html_columns or set()
    visible_indexes = [
        i for i in range(len(headers)) if group_index is None or i != group_index
    ]
    columns: list[dict[str, Any]] = []
    for visible_i, source_i in enumerate(visible_indexes):
        col = {
            "field": _field_name(visible_i),
            "headerName": headers[source_i],
            "sortable": True,
            "filter": True,
            "resizable": True,
        }
        if source_i in html_columns:
            col["cellRenderer"] = "html"
        columns.append(col)

    data: list[dict[str, Any]] = []
    if group_index is None:
        for row in rows:
            data.append(
                {
                    _field_name(i): str(row[source_i] or "")
                    for i, source_i in enumerate(visible_indexes)
                }
            )
    else:
        current_group = object()
        pending_group: dict[str, Any] | None = None
        group_count = 0
        for row in rows:
            group = str(row[group_index] or "Unknown")
            if group != current_group:
                if pending_group is not None:
                    label = (
                        item_label[:-1]
                        if group_count == 1 and item_label.endswith("s")
                        else item_label
                    )
                    pending_group["__groupText"] = (
                        f"{group_label}: {pending_group['__groupName']} "
                        f"({group_count} {label})"
                    )
                current_group = group
                group_count = 0
                pending_group = {
                    "__pdbGroup": True,
                    "__groupName": group,
                    "__groupText": f"{group_label}: {group}",
                }
                data.append(pending_group)
            group_count += 1
            data.append(
                {
                    _field_name(i): str(row[source_i] or "")
                    for i, source_i in enumerate(visible_indexes)
                }
            )
        if pending_group is not None:
            label = (
                item_label[:-1]
                if group_count == 1 and item_label.endswith("s")
                else item_label
            )
            pending_group["__groupText"] = (
                f"{group_label}: {pending_group['__groupName']} "
                f"({group_count} {label})"
            )

    return grid(
        columns,
        data,
        class_name=class_name,
        page_size=page_size,
        height_px=height_px,
        grouped=group_index is not None,
    )


def grid(
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    class_name: str = "",
    page_size: int = 15,
    height_px: int | None = None,
    grouped: bool = False,
) -> str:
    if not rows:
        return '<p class="meta">no data</p>'
    grid_id = f"pdb-grid-{next(_IDS)}"
    height = height_px or min(560, max(220, 92 + min(len(rows), page_size) * 34))
    options = {
        "columnDefs": columns,
        "rowData": rows,
        "theme": "legacy",
        "pagination": len(rows) > page_size,
        "paginationPageSize": page_size,
        "paginationPageSizeSelector": sorted({10, 15, 25, 50, 100, page_size}),
        "domLayout": "normal",
        "grouped": grouped,
    }
    payload = json.dumps(options, ensure_ascii=False).replace("</", "<\\/")
    classes = "pdb-grid ag-theme-quartz"
    if class_name:
        classes += f" {escape(class_name)}"
    return (
        f'<div class="pdb-grid-wrap">'
        f'<div id="{grid_id}" class="{classes}" data-pdb-grid '
        f'style="height:{height}px"></div>'
        f'<script type="application/json" data-pdb-grid-options="{grid_id}">'
        f"{payload}</script></div>"
    )
