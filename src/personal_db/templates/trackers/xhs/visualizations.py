"""Visualizations for the xhs tracker."""

from __future__ import annotations

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


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stat(label: str, value: int | None) -> str:
    return f"<strong>{(value or 0):,}</strong> {label}"


_STYLE = """
<style>
.xhs-posts { display: flex; flex-direction: column; gap: 14px; }
.xhs-post { display: flex; gap: 14px; align-items: stretch;
            padding-bottom: 14px; border-bottom: 1px solid var(--border); }
.xhs-post:last-child { border-bottom: none; padding-bottom: 0; }
.xhs-thumb { flex: 0 0 120px; aspect-ratio: 3/4; border-radius: 6px;
             overflow: hidden; background: var(--bg-inset); display: block; }
.xhs-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.xhs-body { flex: 1; min-width: 0; display: flex; flex-direction: column;
            gap: 5px; font-size: 12px; }
.xhs-title { margin: 0; font-size: 13px; line-height: 1.35; font-weight: 600;
             display: -webkit-box; -webkit-line-clamp: 2;
             -webkit-box-orient: vertical; overflow: hidden; }
.xhs-desc { margin: 0; color: var(--chart-fg); line-height: 1.35;
            display: -webkit-box; -webkit-line-clamp: 2;
            -webkit-box-orient: vertical; overflow: hidden; }
.xhs-meta { margin: 0; font-size: 11px; color: var(--chart-muted); }
.xhs-stats { margin: 0; font-size: 12px; line-height: 1.4; }
.xhs-stats strong { font-weight: 600; }
.xhs-chart { height: 110px; margin-top: 2px; overflow: hidden; }
.xhs-chart svg { width: 100% !important; height: 96px !important; display: block; }
.xhs-cmp { display: flex; flex-direction: column; gap: 18px; }
.xhs-cmp-pane svg { width: 100% !important; height: 230px !important; display: block; }
.xhs-cmp-title { margin: 0 0 4px; font-size: 13px; font-weight: 600;
                 color: var(--chart-fg); text-transform: lowercase; }
.xhs-account { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
               background: var(--bg-inset); }
.xhs-account-main { margin: 0; font-size: 14px; font-weight: 600; }
.xhs-account-meta { margin: 2px 0 0; font-size: 11px; color: var(--chart-muted); }
</style>
"""


def _render_account(con: sqlite3.Connection) -> str:
    try:
        row = con.execute(
            """
            SELECT snapshot_at, nickname, following_count, followers_count,
                   liked_collected_count, visible_note_count
            FROM xhs_account_snapshots
            ORDER BY snapshot_at DESC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.OperationalError:
        return ""
    if not row:
        return ""
    snap, nickname, following, followers, liked_collected, visible = row
    stats = [
        f"{followers:,} followers" if followers is not None else "",
        f"{following:,} following" if following is not None else "",
        f"{liked_collected:,} likes+collects" if liked_collected is not None else "",
        f"{visible:,} visible notes" if visible is not None else "",
    ]
    stats_text = " · ".join(s for s in stats if s)
    return (
        '<div class="xhs-account">'
        f'<p class="xhs-account-main">{escape(nickname or "XHS profile")}</p>'
        f'<p class="xhs-account-meta">{escape(stats_text or "profile snapshot")}'
        f'<br>latest {escape((snap or "")[:16].replace("T", " "))} UTC</p>'
        "</div>"
    )


def _render_post(con: sqlite3.Connection, post: tuple) -> str:
    note_id, title, desc, url, thumb, posted_at = post
    snapshots = con.execute(
        """
        SELECT snapshot_at, view_count, liked_count, collected_count, comment_count, share_count
        FROM xhs_note_snapshots
        WHERE note_id = ?
        ORDER BY snapshot_at ASC
        """,
        (note_id,),
    ).fetchall()

    title_text = (title or "").strip() or "(untitled)"
    desc_text = (desc or "").strip()
    thumb_img = f'<img src="{escape(thumb)}" alt="">' if thumb else ""
    thumb_html = (
        f'<a class="xhs-thumb" href="{escape(url)}" target="_blank">{thumb_img}</a>'
        if url
        else f'<div class="xhs-thumb">{thumb_img}</div>'
    )

    if snapshots:
        latest = snapshots[-1]
        _, views, likes, collects, comments, shares = latest
        snap_note = f"{len(snapshots)} snapshot{'s' if len(snapshots) != 1 else ''}"
        stats = (
            f"{_stat('views', views)} · {_stat('likes', likes)} · {_stat('collects', collects)} · "
            f"{_stat('comments', comments)} · {_stat('shares', shares)}"
        )
        labels = [s[0][5:16].replace("T", " ") for s in snapshots]
        x_ts = [
            _parse_iso(s[0]).timestamp() if _parse_iso(s[0]) else float(i)
            for i, s in enumerate(snapshots)
        ]
        show_every = max(1, len(labels) // 6)
        chart = multi_line_chart(
            labels,
            series=[
                ("views", [s[1] for s in snapshots], "var(--chart-accent)"),
                ("likes", [s[2] for s in snapshots], "#c23b55"),
                ("collects", [s[3] for s in snapshots], "#c27b2d"),
                ("comments", [s[4] for s in snapshots], "#4f8f55"),
            ],
            x_values=x_ts,
            height_px=96,
            show_every_nth_label=show_every,
            annotate_extremes=False,
            viewbox_width=900,
        )
        stats_html = f'<p class="xhs-stats">{stats}</p><div class="xhs-chart">{chart}</div>'
    else:
        snap_note = "no snapshots yet"
        stats_html = '<p class="xhs-stats" style="color:var(--chart-muted)">awaiting first status fetch</p>'

    return (
        '<div class="xhs-post">'
        + thumb_html
        + '<div class="xhs-body">'
        + f'<p class="xhs-title">{escape(title_text)}</p>'
        + (f'<p class="xhs-desc">{escape(desc_text)}</p>' if desc_text else "")
        + f'<p class="xhs-meta">posted {(posted_at or "")[:10]} · {snap_note}</p>'
        + stats_html
        + "</div></div>"
    )


def render_recent_posts(cfg: Config) -> str:
    con = _connect(cfg)
    if con is None:
        return '<p class="meta">no data</p>'
    try:
        posts = con.execute(
            """
            SELECT note_id, title, description, permalink, thumbnail_url, posted_at
            FROM xhs_notes
            WHERE COALESCE(is_archived, 0) = 0
            ORDER BY posted_at DESC
            LIMIT 15
            """
        ).fetchall()
        if not posts:
            return (
                '<p class="meta">no XHS posts ingested yet. run '
                "<code>personal-db sync xhs</code></p>"
            )
        account = _render_account(con)
        cards = [_render_post(con, post) for post in posts]
    except sqlite3.OperationalError:
        return '<p class="meta">xhs not synced yet</p>'
    finally:
        con.close()
    return _STYLE + '<div class="xhs-posts">' + account + "".join(cards) + "</div>"


def render_posts_compared(cfg: Config) -> str:
    con = _connect(cfg)
    if con is None:
        return '<p class="meta">no data</p>'
    try:
        posts = con.execute(
            """
            SELECT note_id, title, posted_at
            FROM xhs_notes
            WHERE COALESCE(is_archived, 0) = 0
            ORDER BY posted_at DESC
            LIMIT 10
            """
        ).fetchall()
        series_rows: dict[str, tuple[str, str, list[tuple]]] = {}
        for note_id, title, posted_at in posts:
            rows = con.execute(
                """
                SELECT snapshot_at, view_count, liked_count, collected_count, comment_count
                FROM xhs_note_snapshots
                WHERE note_id = ?
                ORDER BY snapshot_at ASC
                """,
                (note_id,),
            ).fetchall()
            if rows:
                series_rows[note_id] = (title or note_id, posted_at, rows)
    except sqlite3.OperationalError:
        return '<p class="meta">xhs not synced yet</p>'
    finally:
        con.close()

    if not series_rows:
        return '<p class="meta">no XHS status snapshots yet</p>'

    all_hours: set[int] = set()
    per_post: dict[str, tuple[str, dict[int, tuple[int | None, ...]]]] = {}
    for note_id, (title, posted_at, rows) in series_rows.items():
        posted = _parse_iso(posted_at)
        hour_map: dict[int, tuple[int | None, ...]] = {}
        for snap_at, views, likes, collects, comments in rows:
            snap = _parse_iso(snap_at)
            if posted and snap:
                hour = max(0, round((snap - posted).total_seconds() / 3600))
            else:
                hour = len(hour_map)
            hour_map[hour] = (views, likes, collects, comments)
            all_hours.add(hour)
        per_post[note_id] = (title, hour_map)

    hours = sorted(all_hours)
    labels = [f"{h}h" if h < 48 else f"{h // 24}d" for h in hours]
    palette = [
        "#c23b55",
        "var(--chart-accent)",
        "#4f8f55",
        "#c27b2d",
        "#7057a8",
        "#2f8f8a",
        "#9b4f64",
        "#6b7f2a",
        "#a85f2d",
        "#3f5f9f",
    ]

    def build_series(index: int) -> list[tuple[str, list[int | None], str]]:
        out = []
        for i, (_note_id, (title, hour_map)) in enumerate(per_post.items()):
            values = [hour_map.get(h, (None, None, None, None))[index] for h in hours]
            out.append((title[:36], values, palette[i % len(palette)]))
        return out

    show_every = max(1, len(labels) // 8)
    kwargs = dict(
        x_values=[float(h) for h in hours],
        height_px=230,
        show_every_nth_label=show_every,
        annotate_extremes=False,
        viewbox_width=1200,
        connect_gaps=True,
    )
    views = multi_line_chart(labels, series=build_series(0), **kwargs)
    likes = multi_line_chart(labels, series=build_series(1), **kwargs)
    collects = multi_line_chart(labels, series=build_series(2), **kwargs)
    comments = multi_line_chart(labels, series=build_series(3), **kwargs)
    return _STYLE + (
        '<div class="xhs-cmp">'
        '<div class="xhs-cmp-pane"><p class="xhs-cmp-title">views</p>'
        f"{views}</div>"
        '<div class="xhs-cmp-pane"><p class="xhs-cmp-title">likes</p>'
        f"{likes}</div>"
        '<div class="xhs-cmp-pane"><p class="xhs-cmp-title">collects</p>'
        f"{collects}</div>"
        '<div class="xhs-cmp-pane"><p class="xhs-cmp-title">comments</p>'
        f"{comments}</div>"
        "</div>"
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "recent_posts",
            "name": "Recent XHS Posts",
            "description": "Recent Xiaohongshu posts with latest status counts and trends.",
            "render": render_recent_posts,
        },
        {
            "slug": "posts_compared",
            "name": "XHS Posts Compared",
            "description": "View, like, collect, and comment growth by hours since posting.",
            "render": render_posts_compared,
        },
    ]
