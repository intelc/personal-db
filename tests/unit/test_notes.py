import stat

import pytest

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.core.notes import list_notes, read_note, write_note


def test_init_db_creates_private_database_file(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    assert stat.S_IMODE(cfg.db_path.stat().st_mode) == 0o600


def test_write_and_list_note(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    rel = write_note(cfg, title="weekly", body="# Weekly\nstuff")
    notes = list_notes(cfg)
    assert any(n["path"] == rel for n in notes)
    assert read_note(cfg, rel).startswith("# Weekly")


def test_list_notes_picks_up_unindexed_files(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    (cfg.notes_dir / "2026-04-01-foo.md").write_text("# foo\nbar")
    notes = list_notes(cfg)
    assert any(n["title"] == "2026-04-01-foo" for n in notes)


def test_read_note_rejects_path_traversal(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_root / "outside.md"
    outside.write_text("secret")

    with pytest.raises(ValueError, match="escapes notes dir"):
        read_note(cfg, "../outside.md")


def test_read_note_rejects_symlink_escape(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_root / "outside.md"
    outside.write_text("secret")
    (cfg.notes_dir / "linked.md").symlink_to(outside)

    with pytest.raises(ValueError, match="escapes notes dir"):
        read_note(cfg, "linked.md")
