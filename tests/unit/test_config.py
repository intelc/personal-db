from pathlib import Path

import yaml

from personal_db.core.config import DEFAULT_ROOT, Config, load_config


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


def test_user_name_tokens_defaults_empty_without_config_yaml(tmp_path):
    cfg = Config(root=tmp_path)
    assert cfg.user_name_tokens == ()


def test_user_name_tokens_reads_config_yaml(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"user": {"name_tokens": ["Yiheng", " Chen ", ""]}})
    )
    cfg = Config(root=tmp_path)
    assert cfg.user_name_tokens == ("yiheng", "chen")


def test_user_name_tokens_ignores_malformed_config_yaml(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.yaml").write_text("not: [valid, yaml")
    cfg = Config(root=tmp_path)
    assert cfg.user_name_tokens == ()


def test_agent_terminal_defaults_to_disabled_without_config_yaml(tmp_path):
    cfg = Config(root=tmp_path)
    assert cfg.agent_terminal.enabled is False
    assert cfg.agent_terminal.auto_approve is False


def test_agent_terminal_reads_config_yaml(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"agent_terminal": {"enabled": True, "auto_approve": True}})
    )
    cfg = Config(root=tmp_path)
    assert cfg.agent_terminal.enabled is True
    assert cfg.agent_terminal.auto_approve is True


def test_agent_terminal_enabled_without_auto_approve_defaults_that_off(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"agent_terminal": {"enabled": True}}))
    cfg = Config(root=tmp_path)
    assert cfg.agent_terminal.enabled is True
    assert cfg.agent_terminal.auto_approve is False


def test_agent_terminal_ignores_malformed_config_yaml(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.yaml").write_text("not: [valid, yaml")
    cfg = Config(root=tmp_path)
    assert cfg.agent_terminal.enabled is False
