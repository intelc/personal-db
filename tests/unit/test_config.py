from pathlib import Path

import yaml

from personal_db.config import DEFAULT_ROOT, load_config


def test_default_root_path():
    assert DEFAULT_ROOT == "~/personal_db"


def test_load_config_with_explicit_root(tmp_root):
    cfg_path = tmp_root / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({"root": str(tmp_root)}))
    cfg = load_config(cfg_path)
    assert cfg.root == tmp_root
    assert cfg.db_path == tmp_root / "db.sqlite"
    assert cfg.trackers_dir == tmp_root / "trackers"
    assert cfg.entities_dir == tmp_root / "entities"
    assert cfg.notes_dir == tmp_root / "notes"
    assert cfg.state_dir == tmp_root / "state"


def test_load_config_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.root == Path("~/personal_db").expanduser()
