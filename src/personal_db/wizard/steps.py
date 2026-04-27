"""Step handlers for the tracker setup wizard.

Each handler takes a step and a WizardContext and returns a StepResult
(Ok / Failed / Skipped). Handlers MUTATE state (write .env, save oauth
tokens, etc.) and return a structured result the runner can record.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import questionary

from personal_db.config import Config
from personal_db.manifest import (
    CommandTestStep,
    EnvVarStep,
    FdaCheckStep,
    InstructionsStep,
    OAuthStep,
)
from personal_db.oauth import OAuthFlow, exchange_code, save_token
from personal_db.permissions import (
    open_fda_settings_pane,
    probe_sqlite_access,
    responsible_binary_path,
)
from personal_db.wizard.env_file import read_env, upsert_env


@dataclass
class WizardContext:
    cfg: Config
    env_path: Path


@dataclass
class Ok:
    detail: str = "ok"


@dataclass
class Failed:
    reason: str


@dataclass
class Skipped:
    reason: str


StepResult = Ok | Failed | Skipped


def _prompt(message: str, *, secret: bool = False, default: str = "") -> str:
    """Indirection so tests can monkeypatch this single seam."""
    if secret:
        return questionary.password(message, default=default).ask() or ""
    return questionary.text(message, default=default).ask() or ""


def handle_fda_check(step: FdaCheckStep, ctx: WizardContext) -> StepResult:
    """Probe the gated SQLite file. Up to 3 retries with user prompts."""
    probe_path = Path(step.probe_path).expanduser()
    for attempt in range(3):
        r = probe_sqlite_access(probe_path)
        if r.granted:
            return Ok(f"FDA granted for {probe_path}")
        if attempt == 0:
            python_bin = responsible_binary_path()
            print(
                f"\n  ✗ Cannot access {probe_path}\n"
                f"    Reason: {r.reason}\n"
                f"\n  Grant Full Disk Access to the Python interpreter that runs personal-db:\n"
                f"\n    {python_bin}\n"
                f"\n  In System Settings → Privacy & Security → Full Disk Access → +,\n"
                f"  press Cmd+Shift+G and paste the path above.\n"
                f"\n  This is more durable than granting FDA to your terminal app: it works\n"
                f"  regardless of which terminal you use, and it's required for the launchd\n"
                f"  scheduler (which doesn't run through your terminal at all).\n"
                f"\n  Opening System Settings now…\n"
            )
            open_fda_settings_pane()
        _prompt(f"Press Enter once granted (attempt {attempt + 1}/3), or just Enter to retry")
    return Failed(
        f"FDA still denied after 3 attempts: {probe_path}. "
        f"Restart your terminal after granting and try again."
    )


def handle_instructions(step: InstructionsStep, ctx: WizardContext) -> StepResult:
    print("\n" + step.text + "\n")
    _prompt("Press Enter when done")
    return Ok("acknowledged")


def handle_env_var(step: EnvVarStep, ctx: WizardContext) -> StepResult:
    current = read_env(ctx.env_path).get(step.name) or os.environ.get(step.name) or ""
    if current:
        if step.secret:
            display = "••••" + current[-4:] if len(current) >= 4 else "•" * len(current)
        else:
            display = current
        message = f"{step.prompt} (current: {display}, Enter to keep)"
    else:
        message = step.prompt
    new_value = _prompt(message, secret=step.secret)
    final = new_value or current
    if not final:
        if step.optional:
            return Skipped(f"{step.name} left unset (optional)")
        return Failed(f"no value provided for {step.name}")
    upsert_env(ctx.env_path, step.name, final)
    os.environ[step.name] = final  # propagate so test sync sees it
    return Ok(f"{step.name} configured")


def handle_command_test(step: CommandTestStep, ctx: WizardContext) -> StepResult:
    try:
        r = subprocess.run(step.command, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return Failed(f"command timed out: {' '.join(step.command)}")
    except FileNotFoundError as e:
        return Failed(f"command not found: {e}")
    if r.returncode != step.expect_returncode:
        return Failed(
            f"exit {r.returncode} (expected {step.expect_returncode}): {r.stderr.strip()}"
        )
    if step.expect_pattern and not re.search(step.expect_pattern, r.stdout):
        return Failed(f"pattern mismatch: {step.expect_pattern!r} not in output")
    return Ok("command verified")


def handle_oauth(step: OAuthStep, ctx: WizardContext) -> StepResult:
    client_id = os.environ.get(step.client_id_env)
    client_secret = os.environ.get(step.client_secret_env)
    if not client_id or not client_secret:
        return Failed(
            f"missing OAuth credentials: ensure {step.client_id_env} and "
            f"{step.client_secret_env} are set (run env_var steps first)"
        )
    state = secrets.token_urlsafe(16)
    flow = OAuthFlow(state=state, port=step.redirect_port or 0)
    flow.start()
    try:
        redirect_uri = f"http://{step.redirect_host}:{flow.port}{step.redirect_path}"
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if step.scopes:
            params["scope"] = " ".join(step.scopes)
        auth_url = step.auth_url + "?" + urllib.parse.urlencode(params)
        print(f"\n  Opening browser to authorize {step.provider}…")
        print(f"  If it doesn't open, paste this URL manually:\n    {auth_url}\n")
        webbrowser.open(auth_url)
        code = flow.wait_for_code(timeout_s=120)
        if not code:
            return Failed("OAuth timeout (120s): did you complete the redirect in your browser?")
        token = exchange_code(
            token_url=step.token_url,
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
        save_token(ctx.cfg, step.provider, token)
        return Ok(f"OAuth completed for {step.provider}")
    finally:
        flow.shutdown()
