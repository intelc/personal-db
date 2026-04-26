from unittest.mock import patch

import yaml

from personal_db.config import Config
from personal_db.db import init_db
from personal_db.wizard.menu import (
    _format_bundled_choice,
    _format_choice,
    _list_bundled_not_installed,
    _list_trackers,
    run_menu,
)


def _install(tmp_root, name, setup_steps=None):
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": f"{name} tracker",
                "permission_type": "none" if not setup_steps else "api_key",
                "setup_steps": setup_steps or [],
                "time_column": "ts",
                "schema": {
                    "tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )


def test_list_trackers_returns_installed_only(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install(tmp_root, "habits", setup_steps=[])
    _install(
        tmp_root,
        "github_commits",
        setup_steps=[{"type": "env_var", "name": "X", "prompt": "x"}],
    )
    names = _list_trackers(cfg)
    assert set(names) == {"habits", "github_commits"}


def test_format_choice_includes_icon_and_status(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install(tmp_root, "habits", setup_steps=[])
    label = _format_choice(cfg, "habits")
    assert "—" in label  # no setup needed icon
    assert "habits" in label


def test_run_menu_exits_on_done_selection(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install(tmp_root, "habits", setup_steps=[])
    # Select "Done" immediately
    with patch("personal_db.wizard.menu.questionary.select") as sel:
        sel.return_value.ask.return_value = "__DONE__"
        run_menu(cfg)
    assert sel.called


def test_list_bundled_not_installed_excludes_installed(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install(tmp_root, "habits", setup_steps=[])
    not_installed = _list_bundled_not_installed(cfg)
    # habits is installed, so it should NOT be in the not-installed list
    assert "habits" not in not_installed
    # but the other 4 bundled templates should be
    assert {"github_commits", "whoop", "screen_time", "imessage"} <= set(not_installed)


def test_format_bundled_choice_includes_plus_and_description():
    label = _format_bundled_choice("habits")
    assert label.startswith("+ ")
    assert "habits" in label
    assert "not installed" in label
    assert "Manually-logged daily habits" in label  # from the bundled manifest


def test_format_choice_marks_outdated_with_arrow_icon(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install(tmp_root, "habits", setup_steps=[])
    # The _install helper creates a manifest different from the bundled one (empty
    # setup_steps, different structure), so is_outdated returns True.
    label = _format_choice(cfg, "habits")
    assert "⟳" in label
    assert "update available" in label
