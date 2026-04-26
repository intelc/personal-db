"""Step handlers for the tracker setup wizard.

Each handler takes a step and a WizardContext and returns a StepResult
(Ok / Failed / Skipped). Handlers MUTATE state (write .env, save oauth
tokens, etc.) and return a structured result the runner can record.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import questionary

from personal_db.config import Config
from personal_db.manifest import EnvVarStep
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
        return Failed(f"no value provided for {step.name}")
    upsert_env(ctx.env_path, step.name, final)
    os.environ[step.name] = final  # propagate so test sync sees it
    return Ok(f"{step.name} configured")
