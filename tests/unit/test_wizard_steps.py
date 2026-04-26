import os
import sqlite3

from personal_db.config import Config
from personal_db.manifest import EnvVarStep, FdaCheckStep
from personal_db.wizard.env_file import read_env
from personal_db.wizard.steps import (
    Failed,
    Ok,
    WizardContext,
    handle_env_var,
    handle_fda_check,
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


def test_fda_check_returns_ok_when_db_accessible(tmp_root, monkeypatch):
    db = tmp_root / "ok.sqlite"
    sqlite3.connect(db).executescript("CREATE TABLE x (a INT);")
    step = FdaCheckStep(type="fda_check", probe_path=str(db))
    r = handle_fda_check(step, _ctx(tmp_root))
    assert isinstance(r, Ok)


def test_fda_check_failed_after_3_retries_when_denied(tmp_root, monkeypatch):
    """Simulate a denied probe; user presses Enter 3 times without granting; should Fail."""
    monkeypatch.setattr(
        "personal_db.wizard.steps.probe_sqlite_access",
        lambda p: type("R", (), {"granted": False, "reason": "FDA denied"})(),
    )
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
    monkeypatch.setattr("personal_db.wizard.steps.open_fda_settings_pane", lambda: None)
    step = FdaCheckStep(type="fda_check", probe_path="/dev/null/doesnt-matter")
    r = handle_fda_check(step, _ctx(tmp_root))
    assert isinstance(r, Failed)
    assert "FDA" in r.reason or "denied" in r.reason


def test_fda_check_succeeds_on_retry(tmp_root, monkeypatch):
    """First probe denied, user presses Enter, second probe granted → Ok."""
    state = {"calls": 0}

    def probe(_p):
        state["calls"] += 1
        granted = state["calls"] >= 2
        return type("R", (), {"granted": granted, "reason": "ok" if granted else "denied"})()

    monkeypatch.setattr("personal_db.wizard.steps.probe_sqlite_access", probe)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
    monkeypatch.setattr("personal_db.wizard.steps.open_fda_settings_pane", lambda: None)
    step = FdaCheckStep(type="fda_check", probe_path="/dev/null")
    r = handle_fda_check(step, _ctx(tmp_root))
    assert isinstance(r, Ok)
