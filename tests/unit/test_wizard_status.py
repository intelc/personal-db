import yaml

from personal_db.config import Config
from personal_db.wizard.status import (
    compute_icon,
    read_status,
    write_status,
)


def _install_tracker(root, name, setup_steps):
    d = root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": f"{name} tracker",
                "permission_type": "none" if not setup_steps else "api_key",
                "setup_steps": setup_steps,
                "time_column": "ts",
                "schema": {
                    "tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )


def test_read_status_missing_returns_empty(tmp_root):
    assert read_status(Config(root=tmp_root)) == {}


def test_write_then_read_status_roundtrip(tmp_root):
    cfg = Config(root=tmp_root)
    write_status(cfg, "github_commits", success=True, detail="3 rows")
    s = read_status(cfg)
    assert s["github_commits"]["success"] is True
    assert s["github_commits"]["detail"] == "3 rows"


def test_compute_icon_no_setup_steps_returns_dash(tmp_root):
    cfg = Config(root=tmp_root)
    _install_tracker(tmp_root, "habits", [])
    assert compute_icon(cfg, "habits") == "—"


def test_compute_icon_unconfigured_env_var_returns_x(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    _install_tracker(
        tmp_root,
        "github_commits",
        [{"type": "env_var", "name": "GITHUB_TOKEN", "prompt": "tok"}],
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert compute_icon(cfg, "github_commits") == "✗"


def test_compute_icon_configured_no_test_recorded_returns_x(tmp_root, monkeypatch):
    """env var is set but we've never run a test sync → still ✗ until tested."""
    cfg = Config(root=tmp_root)
    _install_tracker(
        tmp_root,
        "github_commits",
        [{"type": "env_var", "name": "GITHUB_TOKEN", "prompt": "tok"}],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    assert compute_icon(cfg, "github_commits") == "✗"


def test_compute_icon_configured_and_test_passed_returns_check(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    _install_tracker(
        tmp_root,
        "github_commits",
        [{"type": "env_var", "name": "GITHUB_TOKEN", "prompt": "tok"}],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    write_status(cfg, "github_commits", success=True, detail="ok")
    assert compute_icon(cfg, "github_commits") == "✓"


def test_compute_icon_configured_but_test_failed_returns_bang(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    _install_tracker(
        tmp_root,
        "github_commits",
        [{"type": "env_var", "name": "GITHUB_TOKEN", "prompt": "tok"}],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    write_status(cfg, "github_commits", success=False, detail="401 Unauthorized")
    assert compute_icon(cfg, "github_commits") == "!"
