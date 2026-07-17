from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from personal_db.core.config import Config
from personal_db.core.installer import list_bundled
from personal_db.core.manifest import load_manifest
from personal_db.templates.trackers.xhs_saved import ingest, visualizations


def test_xhs_saved_is_bundled_and_manifest_loads():
    assert "xhs_saved" in set(list_bundled())

    root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(
        root / "src/personal_db/templates/trackers/xhs_saved/manifest.yaml"
    )
    assert manifest.name == "xhs_saved"
    assert manifest.permission_type == "manual"
    assert {
        "xhs_saved_collections",
        "xhs_saved_posts",
        "xhs_saved_post_snapshots",
    } <= set(manifest.schema.tables)


def test_xhs_saved_dedupes_collected_notes_and_preserves_first_url():
    note_id = "abcdefabcdefabcdefabcdef"
    second_id = "1234567890abcdef12345678"
    rows = ingest._dedupe_collected_notes(
        [
            {
                "note_id": second_id,
                "url": f"https://www.xiaohongshu.com/explore/{second_id}?xsec_source=pc_collect",
                "title": "Newest row",
            },
            {
                "url": f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=first",
                "title": "First title",
            },
            {
                "note_id": note_id,
                "url": f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=second",
                "title": "Second title",
                "xsec_source": "pc_user",
            },
        ]
    )

    assert [row["note_id"] for row in rows] == [second_id, note_id]
    assert [row["latest_seen_rank"] for row in rows] == [1, 2]
    assert rows[1] == {
        "note_id": note_id,
        "url": f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=second",
        "title": "First title",
        "xsec_token": "second",
        "xsec_source": "pc_user",
        "first_seen_url": f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token=first",
        "latest_seen_rank": 2,
    }


def test_xhs_saved_initial_state_note_summary_parser_includes_media():
    note_id = "abcdefabcdefabcdefabcdef"
    html = (
        "<script>window.__INITIAL_STATE__ = "
        '{"note":{"noteDetailMap":{"abcdef":{"note":{'
        f'"noteId":"{note_id}",'
        '"title":"Saved title",'
        '"desc":"Saved body",'
        '"type":"video",'
        '"time":1700000000000,'
        '"user":{"userId":"u1","nickname":"maker"},'
        '"interactInfo":{'
        '"likedCount":"1.2万",'
        '"collectedCount":34,'
        '"commentCount":5,'
        '"shareCount":6'
        "},"
        '"imageList":[{"infoList":[{"url":"https://img.example/a.jpg"}]}],'
        '"video":{"media":{"stream":{"h264":[{"masterUrl":"https://video.example/a.mp4"}]}}}'
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

    assert summary["title"] == "Saved title"
    assert summary["user"]["nickname"] == "maker"
    assert summary["interact"]["liked"] == "1.2万"
    assert summary["images"] == ["https://img.example/a.jpg"]
    assert summary["videos"] == ["https://video.example/a.mp4"]
    assert ingest._snapshot_row(summary, note_id, "2026-05-25T12:00:00+00:00")[
        "liked_count"
    ] == 12_000


def test_xhs_saved_post_row_preserves_existing_detail_when_not_refetched():
    now = "2026-05-25T12:00:00+00:00"
    row = ingest._post_row(
        {
            "note_id": "abcdefabcdefabcdefabcdef",
            "url": "https://www.xiaohongshu.com/explore/abcdefabcdefabcdefabcdef",
            "title": "Grid title",
            "first_seen_url": "https://first.example/note",
            "latest_seen_rank": 7,
        },
        {
            "title": "Fetched title",
            "description": "Fetched body",
            "saved_first_seen_at": "2026-05-01T00:00:00+00:00",
            "fetch_status": "ok",
            "last_fetched_at": "2026-05-02T00:00:00+00:00",
            "image_urls_json": '["https://img.example/a.jpg"]',
            "video_urls_json": "[]",
            "raw_json": "{}",
        },
        now,
    )

    assert row["title"] == "Fetched title"
    assert row["description"] == "Fetched body"
    assert row["saved_first_seen_at"] == "2026-05-01T00:00:00+00:00"
    assert row["saved_last_seen_at"] == now
    assert row["latest_seen_rank"] == 7
    assert row["fetch_status"] == "ok"


def test_xhs_saved_known_note_ids_reads_existing_posts(tmp_path):
    db_path = tmp_path / "db.sqlite"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE xhs_saved_posts (note_id TEXT PRIMARY KEY)")
    con.executemany(
        "INSERT INTO xhs_saved_posts(note_id) VALUES (?)",
        [("ABCDEFABCDEFABCDEFABCDEF",), ("1234567890abcdef12345678",)],
    )
    con.commit()
    con.close()

    assert ingest._known_note_ids(db_path) == {
        "abcdefabcdefabcdefabcdef",
        "1234567890abcdef12345678",
    }


def test_xhs_saved_migrate_schema_adds_latest_seen_rank(tmp_path):
    db_path = tmp_path / "db.sqlite"
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE xhs_saved_posts (
          note_id TEXT PRIMARY KEY,
          saved_first_seen_at TEXT NOT NULL,
          saved_last_seen_at TEXT NOT NULL
        )
        """
    )
    con.commit()
    con.close()

    ingest._migrate_schema(db_path)

    con = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(xhs_saved_posts)")}
    finally:
        con.close()
    assert "latest_seen_rank" in cols


def test_xhs_saved_thumbnail_cache_helpers(tmp_path):
    cfg = Config(root=tmp_path)
    note_id = "abcdefabcdefabcdefabcdef"
    payload = b"\xff\xd8fake-jpeg"

    cache_path = ingest._thumbnail_cache_path(cfg, note_id)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(payload)

    assert ingest._thumbnail_cached(cfg, note_id) is True
    assert visualizations._cached_thumbnail_data_uri(cfg, note_id).startswith(
        "data:image/jpeg;base64,"
    )


def test_xhs_saved_collection_row_records_incremental_stop_metadata():
    row = ingest._collection_row(
        {
            "href": "https://www.xiaohongshu.com/user/profile/u1?tab=fav&subTab=note",
            "title": "Profile",
            "clickedSaved": True,
            "scrolls": 8,
            "expectedCount": 189,
            "incremental": True,
            "stoppedForOverlap": True,
            "overlapRun": 25,
            "overlapStop": 25,
            "knownIdCount": 126,
        },
        [{"note_id": "a"}, {"note_id": "b"}],
        "2026-05-26T12:00:00+00:00",
    )

    raw = json.loads(row["raw_json"])
    assert row["note_count"] == 2
    assert raw["incremental"] is True
    assert raw["stopped_for_overlap"] is True
    assert raw["overlap_run"] == 25
    assert raw["known_id_count"] == 126


def test_xhs_saved_visualizations_listed():
    slugs = {item["slug"] for item in visualizations.list_visualizations()}
    assert {"recent_saved"} <= slugs
