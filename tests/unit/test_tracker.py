import pytest
import yaml

from personal_db.config import Config
from personal_db.db import apply_tracker_schema, connect, init_db
from personal_db.entities import sync_entities_from_yaml
from personal_db.tracker import Tracker


@pytest.fixture
def cfg(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(
        cfg.db_path,
        """
        CREATE TABLE demo (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            value INTEGER
        );
    """,
    )
    return cfg


def test_cursor_get_default_when_unset(cfg):
    t = Tracker(name="demo", cfg=cfg, manifest=None)  # manifest unused for cursor tests
    assert t.cursor.get(default="2020-01-01") == "2020-01-01"


def test_cursor_set_then_get(cfg):
    t = Tracker(name="demo", cfg=cfg, manifest=None)
    t.cursor.set("2026-04-01")
    assert t.cursor.get() == "2026-04-01"


def test_upsert_inserts_new_rows(cfg):
    t = Tracker(name="demo", cfg=cfg, manifest=None)
    t.upsert("demo", [{"id": "a", "ts": "2026-04-01", "value": 1}], key=["id"])
    con = connect(cfg.db_path)
    rows = con.execute("SELECT id, value FROM demo").fetchall()
    assert rows == [("a", 1)]


def test_upsert_updates_existing_rows(cfg):
    t = Tracker(name="demo", cfg=cfg, manifest=None)
    t.upsert("demo", [{"id": "a", "ts": "2026-04-01", "value": 1}], key=["id"])
    t.upsert("demo", [{"id": "a", "ts": "2026-04-01", "value": 2}], key=["id"])
    con = connect(cfg.db_path)
    assert con.execute("SELECT value FROM demo WHERE id='a'").fetchone() == (2,)


def test_tracker_resolve_person(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    (tmp_root / "entities" / "people.yaml").write_text(
        yaml.safe_dump([{"display_name": "Marko", "aliases": ["marko@example.com"]}])
    )
    (tmp_root / "entities" / "topics.yaml").write_text("[]")
    sync_entities_from_yaml(cfg)
    t = Tracker(name="demo", cfg=cfg, manifest=None)
    pid = t.resolve_person("marko@example.com")
    assert pid is not None
