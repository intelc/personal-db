"""Visualizations for the xhs_saved tracker."""

from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path

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


def _thumbnail_cache_path(cfg: Config, note_id: str) -> Path:
    return cfg.state_dir / "xhs_saved_media" / "thumbs" / f"{note_id}.bin"


def _image_mime(body: bytes) -> str:
    if body.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    if body.startswith(b"GIF87a") or body.startswith(b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _cached_thumbnail_data_uri(cfg: Config, note_id: str) -> str:
    try:
        body = _thumbnail_cache_path(cfg, note_id).read_bytes()
    except OSError:
        return ""
    if not body:
        return ""
    encoded = base64.b64encode(body).decode("ascii")
    return f"data:{_image_mime(body)};base64,{encoded}"


_STYLE = """
<style>
.xhs-saved { display: flex; flex-direction: column; gap: 14px; }
.xhs-saved-summary { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
                     background: var(--bg-inset); }
.xhs-saved-summary p { margin: 0; font-size: 12px; color: var(--chart-muted); }
.xhs-saved-summary strong { color: var(--chart-fg); font-size: 14px; }
.xhs-saved-post { display: flex; gap: 14px; align-items: stretch;
                  padding-bottom: 14px; border-bottom: 1px solid var(--border); }
.xhs-saved-post:last-child { border-bottom: none; padding-bottom: 0; }
.xhs-saved-thumb { flex: 0 0 118px; aspect-ratio: 3/4; border-radius: 6px;
                   overflow: hidden; background: var(--bg-inset); display: block; }
.xhs-saved-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.xhs-saved-body { flex: 1; min-width: 0; display: flex; flex-direction: column;
                  gap: 5px; font-size: 12px; }
.xhs-saved-title { margin: 0; font-size: 13px; line-height: 1.35; font-weight: 600;
                   display: -webkit-box; -webkit-line-clamp: 2;
                   -webkit-box-orient: vertical; overflow: hidden; }
.xhs-saved-desc { margin: 0; color: var(--chart-fg); line-height: 1.35;
                  display: -webkit-box; -webkit-line-clamp: 2;
                  -webkit-box-orient: vertical; overflow: hidden; }
.xhs-saved-meta { margin: 0; font-size: 11px; color: var(--chart-muted); }
.xhs-saved-stats { margin: 0; font-size: 12px; line-height: 1.4; }
.xhs-saved-stats strong { font-weight: 600; }
.xhs-saved-chart { height: 96px; margin-top: 2px; overflow: hidden; }
.xhs-saved-chart svg { width: 100% !important; height: 84px !important; display: block; }
</style>
"""


def _latest_collection(con: sqlite3.Connection) -> str:
    row = con.execute(
        """
        SELECT collected_at, note_count, clicked_saved_tab
        FROM xhs_saved_collections
        ORDER BY collected_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return ""
    collected_at, note_count, clicked = row
    clicked_text = "saved tab opened" if clicked else "saved tab not confirmed"
    return (
        '<div class="xhs-saved-summary">'
        f'<p><strong>{note_count:,}</strong> saved posts · {escape(clicked_text)}'
        f'<br>latest {escape((collected_at or "")[:16].replace("T", " "))} UTC</p>'
        "</div>"
    )


def _media_count(images_json: str | None, videos_json: str | None) -> str:
    try:
        images = len(json.loads(images_json or "[]"))
    except (TypeError, ValueError):
        images = 0
    try:
        videos = len(json.loads(videos_json or "[]"))
    except (TypeError, ValueError):
        videos = 0
    parts = []
    if images:
        parts.append(f"{images} image{'s' if images != 1 else ''}")
    if videos:
        parts.append(f"{videos} video{'s' if videos != 1 else ''}")
    return " · ".join(parts)


def _render_post(cfg: Config, con: sqlite3.Connection, post: tuple) -> str:
    (
        note_id,
        title,
        desc,
        author,
        url,
        thumb,
        saved_at,
        status,
        fetch_error,
        latest_rank,
        images_json,
        videos_json,
    ) = post
    snapshots = con.execute(
        """
        SELECT snapshot_at, liked_count, collected_count, comment_count, share_count
        FROM xhs_saved_post_snapshots
        WHERE note_id = ?
        ORDER BY snapshot_at ASC
        """,
        (note_id,),
    ).fetchall()

    title_text = (title or "").strip() or "(untitled)"
    desc_text = (desc or "").strip()
    thumb_src = _cached_thumbnail_data_uri(cfg, note_id) or thumb
    thumb_img = f'<img src="{escape(thumb_src)}" alt="">' if thumb_src else ""
    thumb_html = (
        f'<a class="xhs-saved-thumb" href="{escape(url)}" target="_blank">{thumb_img}</a>'
        if url
        else f'<div class="xhs-saved-thumb">{thumb_img}</div>'
    )
    meta_parts = [
        f"saved {(saved_at or '')[:10]}",
        f"rank {latest_rank}" if latest_rank else "",
        author or "",
        status or "",
        _media_count(images_json, videos_json),
    ]
    meta = " · ".join(x for x in meta_parts if x)

    if snapshots:
        latest = snapshots[-1]
        _, likes, collects, comments, shares = latest
        stats = (
            f"{_stat('likes', likes)} · {_stat('collects', collects)} · "
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
                ("likes", [s[1] for s in snapshots], "#c23b55"),
                ("collects", [s[2] for s in snapshots], "#c27b2d"),
                ("comments", [s[3] for s in snapshots], "#4f8f55"),
            ],
            x_values=x_ts,
            height_px=84,
            show_every_nth_label=show_every,
            annotate_extremes=False,
            viewbox_width=900,
        )
        stats_html = f'<p class="xhs-saved-stats">{stats}</p><div class="xhs-saved-chart">{chart}</div>'
    elif status == "error":
        err = (fetch_error or "").strip() or "detail fetch failed"
        stats_html = (
            f'<p class="xhs-saved-stats" style="color:var(--chart-muted)" title="{escape(err)}">'
            "detail fetch blocked</p>"
        )
    else:
        stats_html = '<p class="xhs-saved-stats" style="color:var(--chart-muted)">detail fetch pending</p>'

    return (
        '<div class="xhs-saved-post">'
        + thumb_html
        + '<div class="xhs-saved-body">'
        + f'<p class="xhs-saved-title">{escape(title_text)}</p>'
        + (f'<p class="xhs-saved-desc">{escape(desc_text)}</p>' if desc_text else "")
        + f'<p class="xhs-saved-meta">{escape(meta)}</p>'
        + stats_html
        + "</div></div>"
    )


def render_recent_saved(cfg: Config) -> str:
    con = _connect(cfg)
    if con is None:
        return '<p class="meta">no data</p>'
    try:
        posts = con.execute(
            """
            SELECT note_id, title, description, author_nickname, source_url,
                   thumbnail_url, saved_first_seen_at, fetch_status,
                   fetch_error, latest_seen_rank, image_urls_json, video_urls_json
            FROM xhs_saved_posts
            ORDER BY saved_last_seen_at DESC,
                     COALESCE(latest_seen_rank, 999999) ASC,
                     saved_first_seen_at DESC
            LIMIT 20
            """
        ).fetchall()
        if not posts:
            return (
                '<p class="meta">no XHS saved posts ingested yet. run '
                "<code>personal-db sync xhs_saved</code></p>"
            )
        summary = _latest_collection(con)
        cards = [_render_post(cfg, con, post) for post in posts]
    except sqlite3.OperationalError:
        return '<p class="meta">xhs_saved not synced yet</p>'
    finally:
        con.close()
    return _STYLE + '<div class="xhs-saved">' + summary + "".join(cards) + "</div>"


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "recent_saved",
            "title": "Recent XHS Saved Posts",
            "render": render_recent_saved,
        }
    ]
