import os

from personal_db.config import Config
from personal_db.manifest import EnvVarStep
from personal_db.wizard.env_file import read_env
from personal_db.wizard.steps import (
    Failed,
    Ok,
    WizardContext,
    handle_env_var,
)


def _ctx(tmp_root) -> WizardContext:
    return WizardContext(cfg=Config(root=tmp_root), env_path=tmp_root / ".env")


def test_env_var_writes_value_when_missing(tmp_root, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda prompt, **kw: "abc123")
    ctx = _ctx(tmp_root)
    step = EnvVarStep(type="env_var", name="GITHUB_TOKEN", prompt="tok", secret=True)
    r = handle_env_var(step, ctx)
    assert isinstance(r, Ok)
    assert read_env(ctx.env_path) == {"GITHUB_TOKEN": "abc123"}


def test_env_var_keeps_current_when_empty_input(tmp_root, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    ctx = _ctx(tmp_root)
    ctx.env_path.write_text("GITHUB_TOKEN=existing\n")
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda prompt, **kw: "")
    step = EnvVarStep(type="env_var", name="GITHUB_TOKEN", prompt="tok")
    r = handle_env_var(step, ctx)
    assert isinstance(r, Ok)
    assert read_env(ctx.env_path) == {"GITHUB_TOKEN": "existing"}


def test_env_var_failed_when_no_value_and_no_input(tmp_root, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda prompt, **kw: "")
    ctx = _ctx(tmp_root)
    step = EnvVarStep(type="env_var", name="GITHUB_TOKEN", prompt="tok")
    r = handle_env_var(step, ctx)
    assert isinstance(r, Failed)
    assert "no value" in r.reason.lower()


def test_env_var_updates_os_environ_after_write(tmp_root, monkeypatch):
    """After writing to .env, the new value should be visible to subsequent
    sync calls in the same process (the wizard runs them in-process)."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda prompt, **kw: "newval")
    ctx = _ctx(tmp_root)
    step = EnvVarStep(type="env_var", name="GITHUB_TOKEN", prompt="tok")
    handle_env_var(step, ctx)
    assert os.environ.get("GITHUB_TOKEN") == "newval"
