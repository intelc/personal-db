import yaml

from personal_db.config import Config
from personal_db.db import connect, init_db
from personal_db.entities import resolve_person, sync_entities_from_yaml


def write_people_yaml(tmp_root, data):
    p = tmp_root / "entities" / "people.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_sync_creates_people_and_aliases(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    write_people_yaml(
        tmp_root,
        [
            {"display_name": "Marko Chen", "aliases": ["marko@example.com", "+15551234567"]},
        ],
    )
    (tmp_root / "entities" / "topics.yaml").write_text("[]")
    sync_entities_from_yaml(cfg)
    con = connect(cfg.db_path)
    assert con.execute("SELECT display_name FROM people").fetchall() == [("Marko Chen",)]
    aliases = {r[0] for r in con.execute("SELECT alias FROM people_aliases")}
    assert aliases == {"marko@example.com", "+15551234567"}


def test_resolve_person_existing_alias(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    write_people_yaml(
        tmp_root,
        [
            {"display_name": "Marko Chen", "aliases": ["marko@example.com"]},
        ],
    )
    (tmp_root / "entities" / "topics.yaml").write_text("[]")
    sync_entities_from_yaml(cfg)
    pid = resolve_person(cfg, "marko@example.com")
    assert pid is not None


def test_resolve_person_unknown_auto_creates(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    (tmp_root / "entities" / "people.yaml").write_text("[]")
    (tmp_root / "entities" / "topics.yaml").write_text("[]")
    sync_entities_from_yaml(cfg)
    pid = resolve_person(cfg, "newperson@example.com", auto_create=True)
    assert pid is not None
    needs_review = (tmp_root / "state" / "entities_needs_review.jsonl").read_text()
    assert "newperson@example.com" in needs_review
