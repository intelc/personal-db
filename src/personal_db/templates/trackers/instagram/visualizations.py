"""Visualizations for the instagram tracker."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from html import escape

from personal_db.config import Config
from personal_db.ui.charts import multi_line_chart


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def _parse_iso(s: str) -> datetime:
    """IG returns timestamps with `+0000`; our snapshots use `+00:00`.
    fromisoformat in 3.11+ accepts both, but normalize for safety."""
    if s.endswith("+0000"):
        s = s[:-5] + "+00:00"
    return datetime.fromisoformat(s)


def _format_elapsed(hours: int) -> str:
    """0..47 → 'Nh', 2..13 days → 'Nd', 2..7 weeks → 'Nw', else 'Nmo'."""
    if hours < 48:
        return f"{hours}h"
    days = hours // 24
    if days < 14:
        return f"{days}d"
    weeks = days // 7
    if weeks < 8:
        return f"{weeks}w"
    return f"{days // 30}mo"


def _format_terminal(n: float) -> str:
    """Compact form for projected view-count labels: 1.2K / 25K / 1.4M."""
    n = max(0, round(n))
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.0f}K"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def _logistic(t: float, K: float, r: float, t0: float) -> float:
    arg = -r * (t - t0)
    if arg > 500:
        return 0.0
    if arg < -500:
        return K
    return K / (1.0 + math.exp(arg))


def _nelder_mead(
    f, x0: list[float], *, max_iter: int = 600, tol: float = 1e-10
) -> tuple[list[float], float]:
    """Pure-Python Nelder-Mead simplex minimization. Returns (best_x, best_f)."""
    n = len(x0)
    simplex = [list(x0)]
    for i in range(n):
        v = list(x0)
        v[i] = v[i] + (0.5 if v[i] == 0 else 0.1 * (abs(v[i]) + 0.5))
        simplex.append(v)
    fvals = [f(p) for p in simplex]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda i: fvals[i])
        simplex = [simplex[i] for i in order]
        fvals = [fvals[i] for i in order]
        if fvals[-1] - fvals[0] < tol:
            break
        centroid = [sum(simplex[i][k] for i in range(n)) / n for k in range(n)]
        worst = simplex[-1]
        reflected = [centroid[k] + (centroid[k] - worst[k]) for k in range(n)]
        fr = f(reflected)
        if fvals[0] <= fr < fvals[-2]:
            simplex[-1] = reflected
            fvals[-1] = fr
        elif fr < fvals[0]:
            expanded = [centroid[k] + 2 * (centroid[k] - worst[k]) for k in range(n)]
            fe = f(expanded)
            if fe < fr:
                simplex[-1] = expanded
                fvals[-1] = fe
            else:
                simplex[-1] = reflected
                fvals[-1] = fr
        else:
            contracted = [centroid[k] + 0.5 * (worst[k] - centroid[k]) for k in range(n)]
            fc = f(contracted)
            if fc < fvals[-1]:
                simplex[-1] = contracted
                fvals[-1] = fc
            else:
                best = simplex[0]
                for i in range(1, n + 1):
                    simplex[i] = [
                        best[k] + 0.5 * (simplex[i][k] - best[k]) for k in range(n)
                    ]
                    fvals[i] = f(simplex[i])
    return simplex[0], fvals[0]


def _fit_logistic(
    points: list[tuple[float, float]],
) -> tuple[float, float, float] | None:
    """Fit V(t) = K / (1 + exp(-r*(t - t0))) to (t, V) pairs.

    Pre-saturation logistic data fundamentally underdetermines K: many (K, r,
    t0) triples produce near-identical SSE and a naive least-squares fit
    collapses to K ≈ V_now, which makes the "terminal" indistinguishable from
    the last observed value. We address that two ways:

    1. **Soft floor on K**: K must accommodate at least `floor_hours` of
       continued growth at the recently-observed rate, where `floor_hours`
       scales with the reel's age (clamped to 48h..168h). This means a reel
       still gaining views per hour gets a K that reflects future trajectory,
       not just current saturation level.
    2. **Nelder-Mead with multi-start**: nonlinear optimization over
       (log K, log r, t0) starting from several initializations, picking the
       lowest SSE. Multi-start escapes the local minimum at K ≈ V_now.

    The floor is implemented as a quadratic penalty rather than a hard bound,
    so a reel showing genuine deceleration can still slip below it.
    """
    pts = sorted((t, y) for t, y in points if y is not None and y > 0)
    if len(pts) < 5:
        return None
    ts = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    V_now = pts[-1][1]
    age = pts[-1][0]
    if V_now <= 0 or age <= 0:
        return None

    # Recent slope from last ~20% of points (min 3) via OLS on V vs t.
    n_late = max(3, len(pts) // 5)
    late = pts[-n_late:]
    tl = [p[0] for p in late]
    vl = [p[1] for p in late]
    mt = sum(tl) / len(tl)
    mv = sum(vl) / len(vl)
    num = sum((tt - mt) * (v - mv) for tt, v in zip(tl, vl, strict=False))
    den = sum((tt - mt) ** 2 for tt in tl)
    s_recent = num / den if den > 0 else 0.0

    floor_hours = min(168.0, max(48.0, age))
    K_floor = max(V_now * 1.05, V_now + max(0.0, s_recent) * floor_hours)

    t_mid = (ts[0] + ts[-1]) / 2

    def loss(x: list[float]) -> float:
        logK, logr, t0 = x
        K = math.exp(logK)
        r = math.exp(logr)
        s = 0.0
        for t, y in zip(ts, ys, strict=False):
            arg = -r * (t - t0)
            if arg > 500:
                pred = 0.0
            elif arg < -500:
                pred = K
            else:
                pred = K / (1.0 + math.exp(arg))
            s += (pred - y) ** 2
        if K_floor > K:
            s += (K_floor - K) ** 2 * 100.0
        return s

    best: tuple[list[float], float] | None = None
    for k_mult in (1.2, 2.0, 5.0):
        for r_init in (0.05, 0.2):
            x0 = [
                math.log(max(K_floor, V_now * k_mult)),
                math.log(r_init),
                t_mid,
            ]
            res, fv = _nelder_mead(loss, x0)
            if best is None or fv < best[1]:
                best = (res, fv)
    if best is None:
        return None
    logK, logr, t0 = best[0]
    K = math.exp(logK)
    r = math.exp(logr)
    if V_now >= K or r <= 0:
        return None
    return K, r, t0


def _project_logistic(
    K: float,
    r: float,
    t0: float,
    last_t: float,
    last_v: float | None = None,
    *,
    n_steps: int = 10,
) -> list[tuple[int, float]]:
    """Generate (hour, V) points from just past last_t toward the K
    asymptote.

    If `last_v` is given, the logistic is reparametrized (via a shifted t0)
    so it passes exactly through (last_t, last_v). Without this, the fit's
    residual at the endpoint produces a visible cliff between the solid
    line's actual last value and the dashed projection's first point.

    Horizon is the max of:
      - 24h (so even tight-r fits show a visible dashed line)
      - 30% of the reel's current age (scales with chart x-extent)
      - time to 95% K from the (shifted) fit
    capped at 1.5x last_t or +72h to keep the x-axis from blowing out.
    """
    if r <= 0:
        return []
    if last_v is not None and 0 < last_v < K:
        t0_eff = last_t + math.log(K / last_v - 1.0) / r
    else:
        t0_eff = t0
    t_95 = t0_eff + math.log(19.0) / r
    min_extend = max(24.0, last_t * 0.3)
    cap = max(last_t * 1.5, last_t + 72.0)
    horizon = min(max(last_t + min_extend, t_95), cap)
    if horizon <= last_t:
        return []
    out: list[tuple[int, float]] = []
    seen: set[int] = set()
    for i in range(1, n_steps + 1):
        t = last_t + (horizon - last_t) * (i / n_steps)
        h = round(t)
        if h <= last_t or h in seen:
            continue
        seen.add(h)
        out.append((h, _logistic(h, K, r, t0_eff)))
    return out


# Inline CSS scoped to the recent-reels widget. Lives next to the markup so
# this single file is self-contained.
_STYLE = """
<style>
.ig-reels { display: flex; flex-direction: column; gap: 14px; }
.ig-reel  { display: flex; gap: 14px; align-items: stretch;
            padding-bottom: 14px; border-bottom: 1px solid var(--border); }
.ig-reel:last-child { border-bottom: none; padding-bottom: 0; }
.ig-thumb { flex: 0 0 110px; aspect-ratio: 9/16; border-radius: 6px;
            overflow: hidden; background: var(--bg-inset); display: block; }
.ig-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.ig-body  { flex: 1; min-width: 0; display: flex; flex-direction: column;
            gap: 4px; font-size: 12px; }
.ig-cap   { margin: 0; font-size: 13px; line-height: 1.35;
            display: -webkit-box; -webkit-line-clamp: 2;
            -webkit-box-orient: vertical; overflow: hidden; }
.ig-meta  { font-size: 11px; color: var(--chart-muted); margin: 0; }
.ig-stats { font-size: 12px; margin: 0; line-height: 1.4; }
.ig-stats strong { font-weight: 600; }
.ig-charts { display: flex; gap: 14px; margin-top: 4px; }
.ig-chart-cell { flex: 1 1 0; min-width: 0; height: 100px; overflow: hidden; }
.ig-chart-cell .meta { font-size: 10px; margin: 0 0 2px; line-height: 1.2; }
.ig-chart-cell svg { width: 100% !important; height: 80px !important;
                     display: block; }
.ig-chart-title { margin: 0 0 2px; font-size: 11px; font-weight: 600;
                  color: var(--chart-fg); text-transform: lowercase; }
.ig-cmp-stack { display: flex; flex-direction: column; gap: 18px; }
.ig-cmp-pane  { width: 100%; }
.ig-cmp-pane .ig-chart-title { font-size: 13px; margin-bottom: 4px; }
.ig-cmp-pane svg { width: 100% !important; height: 240px !important;
                   display: block; }
.ig-cmp-legend { display: flex; flex-wrap: wrap; gap: 6px 12px;
                 margin: 4px 0 14px; font-size: 11px; }
.ig-cmp-chip { display: inline-flex; align-items: center; gap: 5px;
               max-width: 280px; white-space: nowrap; }
.ig-cmp-swatch { display: inline-block; width: 10px; height: 10px;
                 border-radius: 2px; flex: 0 0 10px; }
.ig-cmp-cap    { flex: 1 1 auto; min-width: 0; overflow: hidden;
                 text-overflow: ellipsis; }
.ig-cmp-term   { color: var(--chart-muted); font-variant-numeric: tabular-nums;
                 flex: 0 0 auto; }
.ig-account { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
              background: var(--bg-inset); }
.ig-account-head { display: flex; justify-content: space-between; gap: 12px;
                   align-items: baseline; margin-bottom: 4px; }
.ig-account-title { margin: 0; font-size: 12px; color: var(--chart-muted);
                    text-transform: lowercase; }
.ig-account-main { margin: 0; font-size: 14px; font-weight: 600; }
.ig-account-meta { margin: 0; font-size: 11px; color: var(--chart-muted); }
.ig-account svg { width: 100% !important; height: 76px !important;
                  display: block; }
</style>
"""


def _stat(label: str, n: int | None) -> str:
    return f"<strong>{(n or 0):,}</strong> {label}"


def _render_account_followers(con: sqlite3.Connection) -> str:
    try:
        rows = con.execute(
            """
            SELECT snapshot_at, followers_count, media_count, username
            FROM instagram_account_snapshots
            WHERE followers_count IS NOT NULL
            ORDER BY snapshot_at ASC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return ""
    if not rows:
        return ""

    latest_at, latest_followers, latest_media_count, latest_username = rows[-1]
    first_followers = rows[0][1]
    delta = (latest_followers or 0) - (first_followers or 0)
    delta_text = f"+{delta:,}" if delta >= 0 else f"{delta:,}"
    username_text = f"@{latest_username}" if latest_username else "account"
    media_text = (
        f" · {latest_media_count:,} media" if latest_media_count is not None else ""
    )
    snap_text = f"{len(rows)} snapshot{'s' if len(rows) != 1 else ''}"

    x_labels = [r[0][5:16].replace("T", " ") for r in rows]
    follower_values = [r[1] for r in rows]
    # Position points by actual snapshot time so a 5-day gap between
    # snapshots renders wider than a 1-hour gap, instead of compressing
    # both to the same uniform x-step.
    x_ts = [_parse_iso(r[0]).timestamp() for r in rows]
    show_every = max(1, len(x_labels) // 6)
    chart = multi_line_chart(
        x_labels,
        series=[("followers", follower_values, "var(--chart-accent)")],
        x_values=x_ts,
        height_px=76,
        show_every_nth_label=show_every,
        annotate_extremes=False,
        viewbox_width=900,
    )

    return (
        '<div class="ig-account">'
        '<div class="ig-account-head">'
        '<div>'
        '<p class="ig-account-title">followers</p>'
        f'<p class="ig-account-main">{latest_followers:,}</p>'
        "</div>"
        f'<p class="ig-account-meta">{escape(username_text)}{media_text}<br>'
        f"{escape(delta_text)} since tracking began · {snap_text}</p>"
        "</div>"
        f"{chart}"
        f'<p class="ig-account-meta">latest {escape(latest_at[:16].replace("T", " "))} UTC</p>'
        "</div>"
    )


def _render_card(con: sqlite3.Connection, reel: tuple) -> str:
    media_id, permalink, caption, thumbnail_url, posted_iso = reel

    snapshots = con.execute(
        """
        SELECT snapshot_at, views, reach, likes, comments, shares, saved
        FROM reels_insights_snapshots
        WHERE media_id = ?
        ORDER BY snapshot_at ASC
        """,
        (media_id,),
    ).fetchall()

    cap_html = escape((caption or "").strip() or "(no caption)")
    posted_short = (posted_iso or "")[:10]

    thumb_img = (
        f'<img src="{escape(thumbnail_url)}" alt="">' if thumbnail_url else ""
    )
    thumb_tag = (
        f'<a class="ig-thumb" href="{escape(permalink)}" target="_blank">{thumb_img}</a>'
        if permalink
        else f'<div class="ig-thumb">{thumb_img}</div>'
    )

    if snapshots:
        latest = snapshots[-1]
        _, views, reach, likes, comments, shares, saved = latest
        snap_note = f"{len(snapshots)} snapshot{'s' if len(snapshots) != 1 else ''}"
        stats_html = (
            f'<p class="ig-stats">'
            f"{_stat('views', views)} · {_stat('reach', reach)} · "
            f"{_stat('likes', likes)} · {_stat('comments', comments)} · "
            f"{_stat('shares', shares)} · {_stat('saved', saved)}"
            "</p>"
        )

        x_labels = [s[0][5:16].replace("T", " ") for s in snapshots]
        x_ts = [_parse_iso(s[0]).timestamp() for s in snapshots]
        views_s    = [s[1] for s in snapshots]
        reach_s    = [s[2] for s in snapshots]
        likes_s    = [s[3] for s in snapshots]
        comments_s = [s[4] for s in snapshots]
        shares_s   = [s[5] for s in snapshots]
        saved_s    = [s[6] for s in snapshots]
        show_every = max(1, len(x_labels) // 6)

        # Three charts side-by-side, each on its own scale to avoid the
        # squeeze that happens when one series dwarfs the others (likes
        # typically being ~5x larger than comments/saved). Hover tooltips
        # carry the exact value at each sample point.
        # viewbox_width≈cell width so text renders at near-1:1 horizontal
        # scale instead of being squished. Cells render at ~360-480px on
        # typical viewports.
        # `x_values=x_ts` places points by actual snapshot time so irregular
        # sync intervals (hourly early, daily later) render proportionally.
        chart_kwargs = dict(
            x_values=x_ts,
            show_every_nth_label=show_every,
            height_px=80,
            annotate_extremes=False,
            viewbox_width=400,
        )
        reach_chart = multi_line_chart(
            x_labels,
            series=[
                ("views", views_s, "var(--chart-accent)"),
                ("reach", reach_s, "#cc6644"),
            ],
            **chart_kwargs,
        )
        likes_chart = multi_line_chart(
            x_labels,
            series=[("likes", likes_s, "#e0245e")],
            **chart_kwargs,
        )
        small_chart = multi_line_chart(
            x_labels,
            series=[
                ("comments", comments_s, "#1da1f2"),
                ("shares",   shares_s,   "#17bf63"),
                ("saved",    saved_s,    "#f7b500"),
            ],
            **chart_kwargs,
        )
        chart_block = (
            '<div class="ig-charts">'
            f'<div class="ig-chart-cell">{reach_chart}</div>'
            f'<div class="ig-chart-cell">{likes_chart}</div>'
            f'<div class="ig-chart-cell">{small_chart}</div>'
            "</div>"
        )
    else:
        snap_note = "no snapshots yet"
        stats_html = '<p class="ig-stats" style="color:var(--chart-muted)">awaiting first insights fetch</p>'
        chart_block = ""

    return (
        '<div class="ig-reel">'
        + thumb_tag
        + '<div class="ig-body">'
        + f'<p class="ig-cap">{cap_html}</p>'
        + f'<p class="ig-meta">posted {posted_short} · {snap_note}</p>'
        + stats_html
        + chart_block
        + "</div></div>"
    )


def render_recent_reels(cfg: Config) -> str:
    """One card per reel (newest first): thumbnail, caption, latest stats,
    and a hover-able views+reach time-series mini-chart built from every
    snapshot in `reels_insights_snapshots`."""
    con = _connect(cfg)
    if con is None:
        return '<p class="meta">no data</p>'
    try:
        reels = con.execute(
            """
            SELECT media_id, permalink, caption, thumbnail_url, timestamp
            FROM reels_media
            WHERE media_product_type = 'REELS'
            ORDER BY timestamp DESC
            LIMIT 15
            """
        ).fetchall()
        if not reels:
            return (
                '<p class="meta">no reels ingested yet. run '
                "<code>personal-db sync instagram</code></p>"
            )
        account = _render_account_followers(con)
        cards = [_render_card(con, r) for r in reels]
    finally:
        con.close()
    return _STYLE + '<div class="ig-reels">' + account + "".join(cards) + "</div>"


def render_reels_compared(cfg: Config) -> str:
    """Cross-reel comparison: x-axis is hours since posting, so all reels
    line up at "0h" regardless of when they were actually published. Same
    three columns as the per-reel viz but each chart now overlays one
    line per reel instead of multiple metrics for one reel."""
    con = _connect(cfg)
    if con is None:
        return '<p class="meta">no data</p>'
    try:
        reels = con.execute(
            """
            SELECT media_id, caption, permalink, thumbnail_url, timestamp
            FROM reels_media
            WHERE media_product_type = 'REELS'
            ORDER BY timestamp DESC
            LIMIT 10
            """
        ).fetchall()
        if not reels:
            return '<p class="meta">no reels ingested yet</p>'
        snaps_by_reel: dict[str, tuple[str, str, str, list]] = {}
        for media_id, caption, permalink, thumb, posted_iso in reels:
            rows = con.execute(
                """
                SELECT snapshot_at, views, likes, saved, reach
                FROM reels_insights_snapshots
                WHERE media_id = ?
                ORDER BY snapshot_at ASC
                """,
                (media_id,),
            ).fetchall()
            if rows:
                snaps_by_reel[media_id] = (
                    caption or "(no caption)",
                    permalink or "",
                    thumb or "",
                    posted_iso,
                    rows,
                )
    finally:
        con.close()

    if not snaps_by_reel:
        return (
            '<p class="meta">no insights snapshots yet — run '
            "<code>personal-db sync instagram</code></p>"
        )

    # Bucket each snapshot's elapsed-time-since-post to the nearest hour
    # so the per-reel sync schedule (hourly/3h/daily) lands on the same
    # x positions across reels and the lines align meaningfully.
    per_reel: dict[str, tuple[str, str, dict[int, tuple]]] = {}
    all_hours: set[int] = set()
    # Latest known reach per reel (rows are ASC so the final iteration wins),
    # used to surface the views/reach rewatch-ratio in the legend.
    latest_reach: dict[str, int] = {}
    for media_id, (caption, permalink, _thumb, posted_iso, rows) in snaps_by_reel.items():
        posted = _parse_iso(posted_iso)
        hour_map: dict[int, tuple] = {}
        for snap_at, views, likes, saved, reach in rows:
            elapsed_h = round(
                (_parse_iso(snap_at) - posted).total_seconds() / 3600
            )
            elapsed_h = max(0, elapsed_h)
            hour_map[elapsed_h] = (views, likes, saved)
            all_hours.add(elapsed_h)
            if reach is not None and reach > 0:
                latest_reach[media_id] = reach
        per_reel[media_id] = (caption, permalink, hour_map)

    # Fit logistic per reel on the views series, then build projection
    # points so the dashed segment extends from the last real snapshot
    # toward the fitted carrying capacity K.
    fits: dict[str, tuple[float, float, float]] = {}  # media_id -> (K, r, t0)
    projections: dict[str, list[tuple[int, float]]] = {}
    for media_id, (_cap, _perma, hour_map) in per_reel.items():
        view_pts = [(float(h), float(d[0])) for h, d in hour_map.items() if d[0] is not None]
        fit = _fit_logistic(view_pts)
        if fit is None:
            continue
        K, r, t0 = fit
        last_h = max(hour_map.keys())
        last_v = hour_map[last_h][0]
        if last_v is None:
            continue
        proj = _project_logistic(K, r, t0, float(last_h), float(last_v))
        if not proj:
            continue
        fits[media_id] = fit
        projections[media_id] = proj
        for h, _v in proj:
            all_hours.add(h)

    sorted_hours = sorted(all_hours)
    x_labels = [_format_elapsed(h) for h in sorted_hours]
    show_every = max(1, len(x_labels) // 8)

    # Hand-picked palette — distinct hues so 10 reels stay distinguishable.
    palette = [
        "var(--chart-accent)", "#e0245e", "#17bf63", "#cc6644", "#1da1f2", "#f7b500",
        "#7a3aa8", "#a83a6e", "#3aa86e", "#a86e3a",
    ]

    views_series: list[tuple] = []
    likes_series: list[tuple] = []
    saved_series: list[tuple] = []
    # legend entry: (caption, color, permalink, terminal_label, ratio_label)
    legend: list[tuple[str, str, str, str, str]] = []

    for i, (media_id, (caption, permalink, hour_map)) in enumerate(per_reel.items()):
        color = palette[i % len(palette)]
        # Suppress per-chart legends with empty name; we render a custom
        # one below so each reel only appears once on the page.
        v_vals: list[float | None] = []
        l_vals: list[float | None] = []
        s_vals: list[float | None] = []
        proj_vals: list[float | None] = []
        proj_map = dict(projections.get(media_id, []))
        last_real_h = max(hour_map.keys()) if hour_map else None
        last_real_v = hour_map[last_real_h][0] if last_real_h is not None else None
        for h in sorted_hours:
            d = hour_map.get(h)
            v_vals.append(d[0] if d else None)
            l_vals.append(d[1] if d else None)
            s_vals.append(d[2] if d else None)
            if h in proj_map:
                proj_vals.append(proj_map[h])
            elif (
                media_id in projections
                and last_real_h is not None
                and h == last_real_h
            ):
                # Anchor projection at the last real data point so the
                # dashed line visually continues from the solid line.
                proj_vals.append(last_real_v)
            else:
                proj_vals.append(None)

        views_series.append(("", v_vals, color))
        if media_id in fits and any(v is not None for v in proj_vals):
            K = fits[media_id][0]
            term_label = _format_terminal(K)
            views_series.append((
                "",
                proj_vals,
                color,
                {
                    "dash": "4,4",
                    "opacity": 0.45,
                    "dots": False,
                    "annotate_extremes": False,
                    "end_label": term_label,
                },
            ))
        else:
            term_label = ""
        likes_series.append(("", l_vals, color))
        saved_series.append(("", s_vals, color))

        # views/reach ratio = rewatch density; >1 means people are replaying
        # the reel, ~1 means each viewer is mostly watching once.
        reach = latest_reach.get(media_id)
        if reach and last_real_v:
            ratio = last_real_v / reach
            ratio_label = f"{ratio:.0f}x" if ratio >= 10 else f"{ratio:.1f}x"
        else:
            ratio_label = ""

        legend.append((caption.strip()[:50], color, permalink, term_label, ratio_label))

    # Each chart spans full width and is taller than the per-reel mini
    # charts, so viewbox_width matches a typical full-width render and
    # height gives lines room to separate visually.
    # `x_values=sorted_hours` makes x-axis proportional to elapsed hours
    # rather than to sample-point index — so the dense early-life hourly
    # snapshots cluster at the left and later daily snapshots spread out,
    # matching how time actually flowed.
    x_hour_values = [float(h) for h in sorted_hours]
    chart_kwargs = dict(
        x_values=x_hour_values,
        show_every_nth_label=show_every,
        height_px=240,
        annotate_extremes=False,
        viewbox_width=1200,
        connect_gaps=True,
    )
    # The views chart shares a y-axis with the projections; widen the
    # upper bound to include the largest fitted K so dashed lines reach
    # their asymptote inside the plot area instead of clipping at the top.
    real_view_max = max(
        (d[0] for _, _, hm in per_reel.values() for d in hm.values() if d[0] is not None),
        default=0,
    )
    fit_k_max = max((k for k, _, _ in fits.values()), default=0)
    views_y_max = (
        max(real_view_max * 1.05, fit_k_max * 1.05) if real_view_max else None
    )
    views_chart = multi_line_chart(
        x_labels, series=views_series, y_max=views_y_max, **chart_kwargs
    )
    likes_chart = multi_line_chart(x_labels, series=likes_series, **chart_kwargs)
    saved_chart = multi_line_chart(x_labels, series=saved_series, **chart_kwargs)

    def _chip(caption: str, color: str, permalink: str, term: str, ratio: str) -> str:
        parts: list[str] = []
        if term:
            parts.append(f"→ {term}")
        if ratio:
            parts.append(ratio)
        suffix_text = " · ".join(parts)
        suffix_html = (
            f'<span class="ig-cmp-term">{escape(suffix_text)}</span>'
            if suffix_text
            else ""
        )
        swatch = f'<span class="ig-cmp-swatch" style="background:{color}"></span>'
        cap_html = f'<span class="ig-cmp-cap">{escape(caption)}</span>'
        body = f"{swatch}{cap_html}{suffix_html}"
        if permalink:
            return (
                f'<a class="ig-cmp-chip" href="{escape(permalink)}" '
                f'target="_blank" style="color:inherit;text-decoration:none">'
                f"{body}</a>"
            )
        return f'<span class="ig-cmp-chip">{body}</span>'

    legend_chips = "".join(_chip(c, col, p, t, r) for c, col, p, t, r in legend)

    return _STYLE + (
        f'<div class="ig-cmp-legend">{legend_chips}</div>'
        '<div class="ig-cmp-stack">'
        '<div class="ig-cmp-pane">'
        '<p class="ig-chart-title">views</p>'
        f"{views_chart}</div>"
        '<div class="ig-cmp-pane">'
        '<p class="ig-chart-title">likes</p>'
        f"{likes_chart}</div>"
        '<div class="ig-cmp-pane">'
        '<p class="ig-chart-title">saves</p>'
        f"{saved_chart}</div>"
        "</div>"
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "recent_reels",
            "name": "Recent Reels",
            "description": (
                "Most recent reels (up to 15). For each: thumbnail, caption, "
                "latest stats, and a time-series of views/reach as it "
                "accumulates across snapshots. Hover the chart for exact "
                "values at each sample point."
            ),
            "render": render_recent_reels,
        },
        {
            "slug": "reels_compared",
            "name": "Reels Compared",
            "description": (
                "Same three metrics (views, likes, saves) but with all "
                "recent reels overlaid on a 'hours since posting' x-axis, "
                "so you can compare growth trajectories at the same "
                "elapsed time. One color per reel; click a legend chip to "
                "open the reel on Instagram."
            ),
            "render": render_reels_compared,
        },
    ]
