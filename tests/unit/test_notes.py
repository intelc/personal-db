from personal_db.config import Config
from personal_db.db import init_db
from personal_db.notes import list_notes, read_note, write_note


def test_write_and_list_note(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    rel = write_note(cfg, title="weekly", body="# Weekly\nstuff")
    notes = list_notes(cfg)
    assert any(n["path"] == rel for n in notes)
    assert read_note(cfg, rel).startswith("# Weekly")
