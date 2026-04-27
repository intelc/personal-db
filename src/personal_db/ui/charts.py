"""Chart-rendering helpers for visualizations.

Pure HTML/CSS — no JS, no SVG libraries. Functions return HTML strings using
the .hbar / .vbar / .heatmap / .calendar / .wordcloud classes from style.css.
Color is used sparingly; defaults to #000 with the option to override per call.
"""

from __future__ import annotations

from datetime import date, timedelta
from html import escape
from typing import Callable


def horizontal_bars(
    items: list[tuple[str, float]],
    *,
    value_fmt: Callable[[float], str] = lambda v: f"{v:g}",
    color: str = "#000",
) -> str:
    """[(label, value), ...] → horizontal bar chart HTML, ranked top-to-bottom."""
    if not items:
        return '<p class="meta">no data</p>'
    max_v = max(v for _, v in items) or 1
    rows = []
    for label, value in items:
        pct = (value / max_v) * 100
        rows.append(
            f"<tr>"
            f'<td class="hbar-label">{escape(str(label))}</td>'
            f'<td class="hbar-cell">'
            f'<div class="hbar" style="width: {pct:.1f}%; background: {color};"></div>'
            f"</td>"
            f'<td class="hbar-val">{escape(value_fmt(value))}</td>'
            f"</tr>"
        )
    return f'<table class="hbar-chart"><tbody>{"".join(rows)}</tbody></table>'


def vertical_bars(
    items: list[tuple[str, float]],
    *,
    color: str = "#000",
    color_fn: Callable[[float], str] | None = None,
    show_every_nth_label: int = 5,
) -> str:
    """[(label, value), ...] → vertical bar chart for time-series."""
    if not items:
        return '<p class="meta">no data</p>'
    max_v = max(v for _, v in items) or 1
    bars = []
    for i, (label, value) in enumerate(items):
        height = (value / max_v) * 100 if value else 0
        c = color_fn(value) if color_fn else color
        show_label = (i % show_every_nth_label == 0) or i == len(items) - 1
        label_html = (
            f'<div class="vbar-label">{escape(str(label))}</div>' if show_label else ""
        )
        bars.append(
            f'<div class="vbar-col" title="{escape(str(label))}: {value:g}">'
            f'<div class="vbar-stem">'
            f'<div class="vbar" style="height: {height:.1f}%; background: {c};"></div>'
            f"</div>"
            f"{label_html}"
            f"</div>"
        )
    return f'<div class="vbar-chart">{"".join(bars)}</div>'


def heatmap(
    grid: list[list[float | None]],
    row_labels: list[str],
    col_labels: list[str],
    *,
    max_value: float | None = None,
    base_color: tuple[int, int, int] = (0, 0, 0),
) -> str:
    """2D grid → CSS heatmap. None / 0 → blank cell. Opacity scales with value."""
    flat = [v for row in grid for v in row if v]
    if not flat:
        return '<p class="meta">no data</p>'
    max_v = max_value or max(flat) or 1
    r, g, b = base_color
    rows_html = []
    for i, row in enumerate(grid):
        cells = []
        for j, v in enumerate(row):
            if not v:
                cells.append('<td class="heat-empty"></td>')
            else:
                opacity = max(0.08, min(1.0, v / max_v))
                title = f"{row_labels[i]} {col_labels[j]}: {v:g}"
                cells.append(
                    f'<td class="heat" '
                    f'style="background: rgba({r},{g},{b},{opacity:.2f})" '
                    f'title="{escape(title)}"></td>'
                )
        rows_html.append(
            f'<tr><td class="heat-rowlabel">{escape(row_labels[i])}</td>'
            f'{"".join(cells)}</tr>'
        )
    col_header = "".join(f"<th>{escape(c)}</th>" for c in col_labels)
    return (
        f'<table class="heatmap">'
        f"<thead><tr><th></th>{col_header}</tr></thead>"
        f'<tbody>{"".join(rows_html)}</tbody></table>'
    )


def calendar_grid(
    values: dict[date, float],
    *,
    end_date: date,
    weeks: int = 13,
    color_fn: Callable[[float], str] | None = None,
    label_fn: Callable[[date, float], str] | None = None,
) -> str:
    """GitHub-style contribution grid: 7 rows × N week-columns.

    color_fn: maps a value to a CSS color (e.g. shade of green by intensity).
              Default: opacity-scaled black.
    label_fn: tooltip renderer. Default: "<date>: <value>".
    """
    if not values:
        return '<p class="meta">no data</p>'
    max_v = max(values.values()) or 1
    # Snap end_date to its week's saturday so the grid right-edge is "today"
    grid_end = end_date
    grid_start = grid_end - timedelta(days=weeks * 7 - 1)
    # Build columns of 7 days each (Sun..Sat or Mon..Sun — we use Mon..Sun)
    cells_by_week: list[list[date | None]] = []
    cur = grid_start
    while cur <= grid_end:
        week = []
        for _ in range(7):
            if cur <= grid_end:
                week.append(cur)
            else:
                week.append(None)
            cur += timedelta(days=1)
        cells_by_week.append(week)

    def _color(v: float) -> str:
        if color_fn:
            return color_fn(v)
        opacity = max(0.1, min(1.0, v / max_v))
        return f"rgba(0,0,0,{opacity:.2f})"

    def _title(d: date, v: float) -> str:
        if label_fn:
            return label_fn(d, v)
        return f"{d.isoformat()}: {v:g}"

    weekday_labels = ["Mon", "", "Wed", "", "Fri", "", "Sun"]
    rows = []
    for wd in range(7):
        cells = []
        for week in cells_by_week:
            d = week[wd]
            if d is None:
                cells.append('<td class="cal-empty"></td>')
                continue
            v = values.get(d, 0)
            if not v:
                cells.append(
                    f'<td class="cal-cell" title="{escape(d.isoformat())}: 0"></td>'
                )
            else:
                cells.append(
                    f'<td class="cal-cell" '
                    f'style="background: {_color(v)};" '
                    f'title="{escape(_title(d, v))}"></td>'
                )
        rows.append(
            f'<tr><td class="cal-rowlabel">{weekday_labels[wd]}</td>{"".join(cells)}</tr>'
        )
    return f'<table class="calendar"><tbody>{"".join(rows)}</tbody></table>'


def stacked_vertical_bars(
    bars: list[dict],
    *,
    show_every_nth_label: int = 5,
    value_unit: str = "",
) -> str:
    """Stacked bar chart for time-series.

    bars: [{"label": str, "segments": [(name, color, value), ...]}, ...]
    Each day-bar's segments stack bottom-up (first segment in the list at the
    bottom). Bar heights are normalized to the max daily total across the
    series, so days with less activity render shorter.
    """
    if not bars:
        return '<p class="meta">no data</p>'
    totals = [sum(v for _, _, v in b["segments"] if v > 0) for b in bars]
    max_total = max(totals) or 1
    cols: list[str] = []
    for i, bar in enumerate(bars):
        total = totals[i]
        if total <= 0:
            inner = ""
        else:
            seg_html: list[str] = []
            for name, color, value in bar["segments"]:
                if value <= 0:
                    continue
                pct_of_max = (value / max_total) * 100
                title = f"{name}: {value:g}{value_unit}"
                seg_html.append(
                    f'<div class="svc-seg" '
                    f'style="height:{pct_of_max:.2f}%; background:{color};" '
                    f'title="{escape(title)}"></div>'
                )
            inner = "".join(seg_html)
        show_label = (i % show_every_nth_label == 0) or i == len(bars) - 1
        label_html = (
            f'<div class="vbar-label">{escape(bar["label"])}</div>' if show_label else ""
        )
        title = f'{bar["label"]}: {total:g}{value_unit}'
        cols.append(
            f'<div class="vbar-col" title="{escape(title)}">'
            f'<div class="svc-stack">{inner}</div>'
            f"{label_html}"
            f"</div>"
        )
    return f'<div class="vbar-chart svc-chart">{"".join(cols)}</div>'


def word_cloud(
    items: list[tuple[str, int]],
    *,
    min_size: int = 11,
    max_size: int = 28,
) -> str:
    """[(word, count), ...] → tag-cloud style HTML. Sizes scale linearly with sqrt(count)
    to compress the long tail (which is what makes the visual readable)."""
    if not items:
        return '<p class="meta">no data</p>'
    import math

    counts = [c for _, c in items]
    lo, hi = min(counts), max(counts)
    span = hi - lo or 1

    def _size(c: int) -> int:
        # sqrt smoothing so the biggest word doesn't dwarf everything else
        t = (math.sqrt(c) - math.sqrt(lo)) / (math.sqrt(hi) - math.sqrt(lo) or 1)
        return int(min_size + t * (max_size - min_size))

    spans = []
    for w, c in items:
        spans.append(
            f'<span class="wc-word" style="font-size:{_size(c)}px;" '
            f'title="{escape(w)}: {c}">{escape(w)}</span>'
        )
    return f'<div class="wordcloud">{"".join(spans)}</div>'
