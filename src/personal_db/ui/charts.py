"""Chart-rendering helpers for visualizations.

Pure HTML/CSS — no JS, no SVG libraries. Functions return HTML strings using
the .hbar / .vbar / .heatmap / .calendar / .wordcloud classes from style.css.
Color is used sparingly; defaults to var(--chart-fg) (theme-aware) with the
option to override per call.
"""

from __future__ import annotations

from datetime import date, timedelta
from html import escape
from typing import Callable


def horizontal_bars(
    items: list[tuple[str, float]],
    *,
    value_fmt: Callable[[float], str] = lambda v: f"{v:g}",
    color: str = "var(--chart-fg)",
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
    color: str = "var(--chart-fg)",
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
    is_default_color = base_color == (0, 0, 0)
    rows_html = []
    for i, row in enumerate(grid):
        cells = []
        for j, v in enumerate(row):
            if not v:
                cells.append('<td class="heat-empty"></td>')
            else:
                opacity = max(0.08, min(1.0, v / max_v))
                title = f"{row_labels[i]} {col_labels[j]}: {v:g}"
                if is_default_color:
                    bg = (
                        "color-mix(in srgb, var(--chart-fg) "
                        f"{opacity * 100:.0f}%, transparent)"
                    )
                else:
                    bg = f"rgba({r},{g},{b},{opacity:.2f})"
                cells.append(
                    f'<td class="heat" '
                    f'style="background: {bg}" '
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
        return f"color-mix(in srgb, var(--chart-fg) {opacity * 100:.0f}%, transparent)"

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


def line_chart(
    items: list[tuple[str, float | None]],
    *,
    color: str = "var(--chart-fg)",
    height_px: int = 140,
    show_every_nth_label: int = 5,
    y_min: float | None = None,
    y_max: float | None = None,
    show_dots: bool = True,
    connect_gaps: bool = True,
    annotate_extremes: bool = True,
    value_attr: str | None = None,
) -> str:
    """[(label, value), ...] → SVG line chart for sparse time-series.

    Defaults connect across None gaps so weigh-ins or other intermittent data
    render as one continuous line. Set `connect_gaps=False` to break the line
    at every None (useful when None means "sensor offline" rather than "no data
    today"). Each dot has a `<title>` SVG tooltip showing date and value.
    With `annotate_extremes=True` (default), the highest and lowest points are
    labelled with their values.

    `value_attr`: if set (e.g. `"data-kg"`), the raw numeric value is also
    written to that HTML attribute on every numeric `<text>` and `<title>` in
    the chart. Lets a host page implement a unit toggle in JS without losing
    the original units.
    """
    return multi_line_chart(
        x_labels=[lbl for lbl, _ in items],
        series=[("", [v for _, v in items], color)],
        height_px=height_px,
        show_every_nth_label=show_every_nth_label,
        y_min=y_min,
        y_max=y_max,
        show_dots=show_dots,
        connect_gaps=connect_gaps,
        annotate_extremes=annotate_extremes,
        value_attr=value_attr,
    )


def multi_line_chart(
    x_labels: list[str],
    series: list,
    *,
    x_values: list[float] | None = None,
    height_px: int = 140,
    show_every_nth_label: int = 5,
    y_min: float | None = None,
    y_max: float | None = None,
    show_dots: bool = True,
    connect_gaps: bool = True,
    annotate_extremes: bool = True,
    value_attr: str | None = None,
    viewbox_width: int = 1000,
) -> str:
    """Multi-series SVG line chart sharing one x-axis.

    `series` is a list of `(name, values, color)` or
    `(name, values, color, style)`. `style` is an optional dict with any of:
      - `dash` (str): SVG `stroke-dasharray`, e.g. `"4,4"`.
      - `opacity` (float): stroke/text opacity 0..1.
      - `width` (float): stroke width (default 1.5).
      - `dots` (bool): override top-level `show_dots` for this series.
      - `annotate_extremes` (bool): override top-level annotate flag.
      - `end_label` (str): text rendered next to the series' last point.

    By default lines connect across None values; pass `connect_gaps=False` to
    break the line at every None instead.

    `x_values`: optional list of numeric x-coordinates (one per label). When
    provided, point x-positions scale to the value range so a 5-day gap is
    visually 5× wider than a 1-day gap. Without it, points are spaced
    uniformly by index — fine for daily series but wrong for irregular ones
    (sparse early samples, dense later ones, etc).

    `value_attr`: if set (e.g. `"data-kg"`), the raw numeric value is also
    written to that HTML attribute on every numeric `<text>` and `<title>` in
    the chart, so a host page can swap displayed text via JS without losing
    the original units.
    """
    def _vattr(v: float) -> str:
        return f' {value_attr}="{v:g}"' if value_attr else ""

    n = len(x_labels)
    if n == 0 or not series:
        return '<p class="meta">no data</p>'
    norm_series: list[tuple[str, list, str, dict]] = []
    for s in series:
        if len(s) == 4:
            norm_series.append((s[0], s[1], s[2], s[3] or {}))
        else:
            norm_series.append((s[0], s[1], s[2], {}))
    series = norm_series
    real = [v for _, vs, _, _ in series for v in vs if v is not None]
    if not real:
        return '<p class="meta">no data</p>'

    lo = y_min if y_min is not None else min(real)
    hi = y_max if y_max is not None else max(real)
    if hi == lo:
        hi = lo + 1

    # viewBox width is the internal coord system. With
    # preserveAspectRatio="none" and the SVG sized via CSS to its container,
    # the content gets stretched non-uniformly: text glyphs render at
    # (actual_width / viewbox_width). For full-page charts the default of
    # 1000 is fine, but multi-column layouts should pass a value closer to
    # the actual rendered pixel width, or text will squish.
    width = viewbox_width
    m_left, m_right, m_top, m_bot = 40, 24, 14, 18
    plot_w = width - m_left - m_right
    plot_h = height_px - m_top - m_bot

    if x_values is not None and len(x_values) == n and n >= 2:
        x_min = min(x_values)
        x_max = max(x_values)
        x_span = (x_max - x_min) if x_max > x_min else 1.0
        _xv = list(x_values)

        def x_for(i: int) -> float:
            return m_left + ((_xv[i] - x_min) / x_span) * plot_w
    else:
        def x_for(i: int) -> float:
            return m_left + (i / max(n - 1, 1)) * plot_w

    def y_for(v: float) -> float:
        return m_top + (1 - (v - lo) / (hi - lo)) * plot_h

    parts: list[str] = []
    for _, values, color, style in series:
        indexed = [(i, v) for i, v in enumerate(values) if v is not None]
        if not indexed:
            continue

        sw = float(style.get("width", 1.5))
        dash = style.get("dash", "")
        op = float(style.get("opacity", 1.0))
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        op_attr = f' stroke-opacity="{op:g}"' if op != 1.0 else ""

        if connect_gaps:
            segs = [[(x_for(i), y_for(v)) for i, v in indexed]]
        else:
            segs = []
            seg: list[tuple[float, float]] = []
            for i, v in enumerate(values):
                if v is None:
                    if seg:
                        segs.append(seg)
                        seg = []
                    continue
                seg.append((x_for(i), y_for(v)))
            if seg:
                segs.append(seg)

        for s in segs:
            if len(s) >= 2:
                pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in s)
                parts.append(
                    f'<polyline points="{pts}" fill="none" '
                    f'stroke="{color}" stroke-width="{sw:g}"{dash_attr}{op_attr}/>'
                )

        if style.get("dots", show_dots):
            for i, v in indexed:
                parts.append(
                    f'<circle cx="{x_for(i):.1f}" cy="{y_for(v):.1f}" r="2.5" '
                    f'fill="{color}"{op_attr.replace("stroke-opacity", "fill-opacity")}>'
                    f"<title{_vattr(v)}>{escape(str(x_labels[i]))}: {v:g}</title>"
                    f"</circle>"
                )

        if style.get("annotate_extremes", annotate_extremes) and len(indexed) >= 2:
            hi_i, hi_v = max(indexed, key=lambda iv: iv[1])
            lo_i, lo_v = min(indexed, key=lambda iv: iv[1])
            if hi_v != lo_v:
                parts.append(
                    f'<text{_vattr(hi_v)} x="{x_for(hi_i):.1f}" y="{y_for(hi_v) - 6:.1f}" '
                    f'font-size="9" text-anchor="middle" fill="{color}">{hi_v:g}</text>'
                )
                parts.append(
                    f'<text{_vattr(lo_v)} x="{x_for(lo_i):.1f}" y="{y_for(lo_v) + 12:.1f}" '
                    f'font-size="9" text-anchor="middle" fill="{color}">{lo_v:g}</text>'
                )

        end_label = style.get("end_label")
        if end_label and indexed:
            last_i, last_v = indexed[-1]
            ex = x_for(last_i)
            ey = y_for(last_v)
            ex = min(ex, width - m_right - 2)
            ey = max(m_top + 8, min(ey, height_px - m_bot - 2))
            parts.append(
                f'<text x="{ex + 3:.1f}" y="{ey + 3:.1f}" '
                f'font-size="10" text-anchor="start" fill="{color}" '
                f'font-weight="700">{escape(str(end_label))}</text>'
            )

    # X-axis tick labels. Anchor first label at start, last at end so they
    # don't clip the SVG viewBox edges.
    for i, lbl in enumerate(x_labels):
        if (i % show_every_nth_label == 0) or i == n - 1:
            anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
            parts.append(
                f'<text x="{x_for(i):.1f}" y="{height_px - 4}" '
                f'font-size="10" text-anchor="{anchor}" fill="var(--chart-muted)">{escape(str(lbl))}</text>'
            )

    def _axis_label(v: float) -> str:
        # Round to integer for axis bounds so float headroom multipliers
        # (e.g. `y_max * 1.05`) don't produce labels like "52003.1" that
        # overflow the left margin. `:g` is fine for small / fractional
        # series like Withings weight where sub-integer precision matters.
        if abs(v) >= 100:
            return f"{int(round(v))}"
        return f"{v:g}"

    parts.append(
        f'<text{_vattr(hi)} x="{m_left - 4:.1f}" y="{m_top + 4:.1f}" '
        f'font-size="10" text-anchor="end" fill="var(--chart-muted)">{_axis_label(hi)}</text>'
        f'<text{_vattr(lo)} x="{m_left - 4:.1f}" y="{m_top + plot_h:.1f}" '
        f'font-size="10" text-anchor="end" fill="var(--chart-muted)">{_axis_label(lo)}</text>'
    )

    legend = ""
    named = [(s[0], s[2]) for s in series if s[0]]
    if named:
        chips = "".join(
            f'<span style="margin-right:1em">'
            f'<span style="display:inline-block;width:.8em;height:.8em;'
            f'background:{c};margin-right:.3em;vertical-align:middle"></span>'
            f"{escape(name)}</span>"
            for name, c in named
        )
        legend = f'<p class="meta" style="margin:0">{chips}</p>'

    return (
        legend
        + f'<svg viewBox="0 0 {width} {height_px}" preserveAspectRatio="none" '
        f'style="width:100%;height:{height_px}px">'
        f'{"".join(parts)}'
        f"</svg>"
    )


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
