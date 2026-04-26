import pytest

from personal_db.config import Config
from personal_db.installer import install_template, list_bundled


def test_list_bundled_returns_known_templates():
    names = set(list_bundled())
    # Exactly these 5 ship with v0.1.
    assert names == {"github_commits", "whoop", "screen_time", "imessage", "habits"}


def test_install_template_copies_tree(tmp_root):
    cfg = Config(root=tmp_root)
    dest = install_template(cfg, "habits")
    assert dest == tmp_root / "trackers" / "habits"
    assert (dest / "manifest.yaml").exists()
    assert (dest / "schema.sql").exists()
    assert (dest / "ingest.py").exists()


def test_install_template_raises_on_already_installed(tmp_root):
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    with pytest.raises(FileExistsError):
        install_template(cfg, "habits")


def test_install_template_raises_on_unknown(tmp_root):
    cfg = Config(root=tmp_root)
    with pytest.raises(ValueError):
        install_template(cfg, "no_such_tracker_xyz")
