import os
import sqlite3
from unittest.mock import MagicMock, patch

from personal_db.config import Config
from personal_db.manifest import (
    CommandTestStep,
    EnvVarStep,
    FdaCheckStep,
    InstructionsStep,
    OAuthStep,
)
from personal_db.oauth import load_token
from personal_db.wizard.env_file import read_env
from personal_db.wizard.steps import (
    Failed,
    Ok,
    Skipped,
    WizardContext,
    handle_command_test,
    handle_env_var,
    handle_fda_check,
    handle_instructions,
    handle_oauth,
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


def test_fda_check_message_includes_python_binary_path(tmp_root, monkeypatch, capsys):
    """When the probe fails, the message must tell the user which binary to grant FDA to."""
    monkeypatch.setattr(
        "personal_db.wizard.steps.probe_sqlite_access",
        lambda p: type("R", (), {"granted": False, "reason": "FDA denied"})(),
    )
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
    monkeypatch.setattr("personal_db.wizard.steps.open_fda_settings_pane", lambda: None)
    step = FdaCheckStep(type="fda_check", probe_path="/dev/null/fake")
    handle_fda_check(step, _ctx(tmp_root))
    out = capsys.readouterr().out
    # The actual python binary path appears in the recommendation
    import sys
    from pathlib import Path

    assert str(Path(sys.executable).resolve()) in out
    # The message names "Python interpreter" so the user knows what they're granting
    assert "Python" in out or "python" in out


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


def test_instructions_always_returns_ok(tmp_root, monkeypatch, capsys):
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
    step = InstructionsStep(
        type="instructions", text="Edit `<root>/entities/people.yaml` to add aliases."
    )
    r = handle_instructions(step, _ctx(tmp_root))
    assert isinstance(r, Ok)
    captured = capsys.readouterr()
    assert "Edit" in captured.out


def test_command_test_ok_on_returncode_match(tmp_root):
    step = CommandTestStep(type="command_test", command=["true"])
    r = handle_command_test(step, _ctx(tmp_root))
    assert isinstance(r, Ok)


def test_command_test_failed_on_returncode_mismatch(tmp_root):
    step = CommandTestStep(type="command_test", command=["false"])
    r = handle_command_test(step, _ctx(tmp_root))
    assert isinstance(r, Failed)


def test_command_test_pattern_match(tmp_root):
    step = CommandTestStep(type="command_test", command=["echo", "hello"], expect_pattern="ell")
    r = handle_command_test(step, _ctx(tmp_root))
    assert isinstance(r, Ok)


def test_command_test_pattern_mismatch_returns_failed(tmp_root):
    step = CommandTestStep(type="command_test", command=["echo", "hello"], expect_pattern="zzz")
    r = handle_command_test(step, _ctx(tmp_root))
    assert isinstance(r, Failed)
    assert "pattern" in r.reason.lower()


def test_oauth_handler_runs_full_flow(tmp_root, monkeypatch):
    monkeypatch.setenv("WHOOP_CLIENT_ID", "CID")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "CS")

    fake_flow = MagicMock()
    fake_flow.port = 12345
    fake_flow.wait_for_code.return_value = "AUTH_CODE"

    fake_token = {"access_token": "AT", "refresh_token": "RT", "expires_at": 9999999999}

    with (
        patch("personal_db.wizard.steps.OAuthFlow", return_value=fake_flow) as flow_cls,
        patch("personal_db.wizard.steps.exchange_code", return_value=fake_token) as ex,
        patch("personal_db.wizard.steps.webbrowser.open") as wb,
    ):
        ctx = _ctx(tmp_root)
        step = OAuthStep(
            type="oauth",
            provider="whoop",
            client_id_env="WHOOP_CLIENT_ID",
            client_secret_env="WHOOP_CLIENT_SECRET",
            auth_url="https://example.com/auth",
            token_url="https://example.com/token",
            scopes=["read:profile"],
        )
        r = handle_oauth(step, ctx)

    assert isinstance(r, Ok)
    assert flow_cls.called
    assert ex.called
    assert wb.called  # we did open the browser
    saved = load_token(ctx.cfg, "whoop")
    assert saved["access_token"] == "AT"


def test_oauth_handler_failed_when_code_never_arrives(tmp_root, monkeypatch):
    monkeypatch.setenv("WHOOP_CLIENT_ID", "CID")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "CS")
    fake_flow = MagicMock()
    fake_flow.port = 12345
    fake_flow.wait_for_code.return_value = None  # timeout

    with (
        patch("personal_db.wizard.steps.OAuthFlow", return_value=fake_flow),
        patch("personal_db.wizard.steps.webbrowser.open"),
    ):
        step = OAuthStep(
            type="oauth",
            provider="whoop",
            client_id_env="WHOOP_CLIENT_ID",
            client_secret_env="WHOOP_CLIENT_SECRET",
            auth_url="https://example.com/auth",
            token_url="https://example.com/token",
        )
        r = handle_oauth(step, _ctx(tmp_root))
    assert isinstance(r, Failed)
    assert "timeout" in r.reason.lower() or "did you complete" in r.reason.lower()


def test_oauth_handler_failed_when_credentials_missing(tmp_root, monkeypatch):
    monkeypatch.delenv("WHOOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("WHOOP_CLIENT_SECRET", raising=False)
    step = OAuthStep(
        type="oauth",
        provider="whoop",
        client_id_env="WHOOP_CLIENT_ID",
        client_secret_env="WHOOP_CLIENT_SECRET",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
    )
    r = handle_oauth(step, _ctx(tmp_root))
    assert isinstance(r, Failed)
    assert "WHOOP_CLIENT_ID" in r.reason or "client" in r.reason.lower()


def test_env_var_optional_empty_returns_skipped(tmp_root, monkeypatch):
    monkeypatch.delenv("MAYBE_KEY", raising=False)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
    ctx = _ctx(tmp_root)
    step = EnvVarStep(type="env_var", name="MAYBE_KEY", prompt="opt", optional=True)
    r = handle_env_var(step, ctx)
    assert isinstance(r, Skipped)
    # Nothing written to .env
    assert not ctx.env_path.exists() or "MAYBE_KEY" not in (
        ctx.env_path.read_text() if ctx.env_path.exists() else ""
    )


def test_env_var_optional_with_value_still_writes(tmp_root, monkeypatch):
    """If user provides a value for an optional field, it's still written."""
    monkeypatch.delenv("MAYBE_KEY", raising=False)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "myvalue")
    ctx = _ctx(tmp_root)
    step = EnvVarStep(type="env_var", name="MAYBE_KEY", prompt="opt", optional=True)
    r = handle_env_var(step, ctx)
    assert isinstance(r, Ok)
    assert read_env(ctx.env_path) == {"MAYBE_KEY": "myvalue"}
