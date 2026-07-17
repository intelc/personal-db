from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from personal_db.core.installer import list_bundled
from personal_db.core.manifest import load_manifest
from personal_db.templates.trackers.xhs import ingest, visualizations


def test_xhs_is_bundled_and_manifest_loads():
    assert "xhs" in set(list_bundled())

    root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(root / "src/personal_db/templates/trackers/xhs/manifest.yaml")
    assert manifest.name == "xhs"
    assert manifest.permission_type == "manual"
    assert {"xhs_notes", "xhs_note_snapshots", "xhs_account_snapshots"} <= set(
        manifest.schema.tables
    )


def test_xhs_count_and_time_parsers():
    assert ingest._parse_count("1.2万") == 12_000
    assert ingest._parse_count("3.4k") == 3_400
    assert ingest._parse_count("1,234") == 1_234
    assert ingest._parse_count("--") is None

    assert ingest._iso_from_xhs_time(1_700_000_000_000) == "2023-11-14T22:13:20+00:00"
    assert ingest._iso_from_xhs_time("2026-05-24T12:00:00Z") == (
        "2026-05-24T12:00:00+00:00"
    )


def test_xhs_flatten_note_prefers_detail_with_collected_fallback():
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC).isoformat()
    row = ingest._flatten_note(
        {
            "note_id": "abcdefabcdefabcdefabcdef",
            "title": "Detail title",
            "desc": "Body",
            "type": "normal",
            "time": 1_700_000_000_000,
            "user": {"id": "user1", "nickname": "Me"},
            "source_url": "https://www.xiaohongshu.com/explore/abcdefabcdefabcdefabcdef",
            "images": ["https://img.example/1.jpg"],
        },
        {
            "note_id": "abcdefabcdefabcdefabcdef",
            "title": "Grid title",
            "url": "https://grid.example/note",
            "thumbnail_url": "https://img.example/thumb.jpg",
        },
        None,
        now,
    )

    assert row["note_id"] == "abcdefabcdefabcdefabcdef"
    assert row["title"] == "Detail title"
    assert row["posted_at"] == "2023-11-14T22:13:20+00:00"
    assert row["thumbnail_url"] == "https://img.example/1.jpg"
    assert row["author_nickname"] == "Me"


def test_xhs_initial_state_note_summary_parser():
    note_id = "abcdefabcdefabcdefabcdef"
    html = (
        "<script>window.__INITIAL_STATE__ = "
        '{"note":{"noteDetailMap":{"abcdef":{"note":{'
        f'"noteId":"{note_id}",'
        '"title":"Post title",'
        '"desc":"Post body",'
        '"type":"video",'
        '"time":1700000000000,'
        '"user":{"userId":"u1","nickname":"intel"},'
        '"interactInfo":{'
        '"likedCount":"1.2万",'
        '"collectedCount":34,'
        '"commentCount":5,'
        '"shareCount":6'
        "},"
        '"imageList":[{"infoList":[{"url":"https://img.example/a.jpg"}]}]'
        "}}}}}"
        "</script>"
    )
    state = ingest._extract_initial_state(html)
    note = ingest._find_note_in_state(state, note_id)
    summary = ingest._note_summary(
        note,
        f"https://www.xiaohongshu.com/explore/{note_id}",
        note_id,
    )

    assert summary["title"] == "Post title"
    assert summary["user"]["nickname"] == "intel"
    assert summary["interact"]["liked"] == "1.2万"
    assert summary["images"] == ["https://img.example/a.jpg"]


def test_xhs_creator_manager_row_parser_marks_archived_and_views():
    parsed = ingest._parse_creator_row(
        {
            "note_id": "693f43a2000000001e02a45d",
            "text": (
                "仅自己可见 SF旧金山转租|Fifteen Fifty 31楼 2B主卧 "
                "发布于 2025年12月15日 07:09 1004 2 8 4 2 权限设置 置顶 编辑 删除"
            ),
            "thumbnail_url": "https://img.example/thumb.jpg",
        }
    )

    assert parsed is not None
    assert parsed["note_id"] == "693f43a2000000001e02a45d"
    assert parsed["is_archived"] == 1
    assert parsed["visibility_label"] == "仅自己可见"
    assert parsed["posted_at"] == "2025-12-14T23:09:00+00:00"
    assert parsed["view_count"] == 1004
    assert parsed["comment_count"] == 2
    assert parsed["liked_count"] == 8
    assert parsed["collected_count"] == 4
    assert parsed["share_count"] == 2


def test_xhs_profile_snapshot_parses_visible_counts():
    now = "2026-05-24T12:00:00+00:00"
    snapshot = ingest._profile_snapshot(
        "https://www.xiaohongshu.com/user/profile/u1",
        {
            "notes": [{"note_id": "a"}],
            "profile": {
                "title": "My Profile - 小红书",
                "text": "关注 12 粉丝 1.5万 获赞与收藏 2.3万",
            },
        },
        now,
    )

    assert snapshot["following_count"] == 12
    assert snapshot["followers_count"] == 15_000
    assert snapshot["liked_collected_count"] == 23_000
    assert snapshot["visible_note_count"] == 1


def test_xhs_visualizations_listed():
    slugs = {item["slug"] for item in visualizations.list_visualizations()}
    assert {"recent_posts", "posts_compared"} <= slugs
