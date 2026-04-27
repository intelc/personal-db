import pytest

from personal_db.config import Config
from personal_db.installer import install_template, is_outdated, list_bundled, update_template


def test_list_bundled_returns_known_templates():
    names = set(list_bundled())
    # These connectors ship with the package; new ones extend this set.
    assert {
        "github_commits",
        "whoop",
        "screen_time",
        "imessage",
        "habits",
        "claude_conversations",
        "codex_conversations",
    } <= names


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


def test_is_outdated_false_when_files_match(tmp_root):
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    assert is_outdated(cfg, "habits") is False


def test_is_outdated_true_when_files_differ(tmp_root):
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    # Mutate one file to simulate drift
    (tmp_root / "trackers" / "habits" / "manifest.yaml").write_text("name: hacked\n")
    assert is_outdated(cfg, "habits") is True


def test_is_outdated_false_when_not_installed(tmp_root):
    cfg = Config(root=tmp_root)
    assert is_outdated(cfg, "habits") is False


def test_is_outdated_false_for_custom_tracker(tmp_root):
    """A user-created tracker (no bundled template) is never marked outdated."""
    cfg = Config(root=tmp_root)
    custom = tmp_root / "trackers" / "my_custom_thing"
    custom.mkdir(parents=True)
    (custom / "manifest.yaml").write_text("name: custom\n")
    assert is_outdated(cfg, "my_custom_thing") is False


def test_update_template_overwrites_files(tmp_root):
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    # Mutate the installed manifest
    p = tmp_root / "trackers" / "habits" / "manifest.yaml"
    p.write_text("name: hacked\n")
    update_template(cfg, "habits")
    # Should be restored from bundle
    assert "hacked" not in p.read_text()
    assert p.read_text().startswith("name: habits")


def test_update_template_preserves_other_files(tmp_root):
    """If the user has a side file (e.g., notes) in the tracker dir, update doesn't touch it."""
    cfg = Config(root=tmp_root)
    install_template(cfg, "habits")
    side = tmp_root / "trackers" / "habits" / "user_notes.md"
    side.write_text("personal notes")
    update_template(cfg, "habits")
    assert side.exists()
    assert side.read_text() == "personal notes"
