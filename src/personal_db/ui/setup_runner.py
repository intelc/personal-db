"""Web wizard glue: list tracker statuses, render setup_steps as form fields,
and process submitted form data through the same logical pipeline as the
terminal wizard (`wizard.runner`) — minus the questionary prompts.

OAuth is intentionally NOT executed via web in v0: the redirect dance between
the dashboard's own browser tab and a freshly-spawned local callback server
is fragile, and the terminal flow already works. Web wizard reports OAuth
steps as "needs terminal" and keeps moving.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from personal_db.config import Config
from personal_db.installer import list_bundled
from personal_db.manifest import (
    CommandTestStep,
    EnvVarStep,
    FdaCheckStep,
    InstructionsStep,
    Manifest,
    OAuthStep,
    load_manifest,
)
from personal_db.permissions import probe_sqlite_access
from personal_db.sync import sync_one
from personal_db.wizard.env_file import read_env, upsert_env
from personal_db.wizard.runner import RunResult
from personal_db.wizard.status import compute_icon, read_status, write_status


@dataclass
class TrackerOverview:
    name: str
    description: str
    installed: bool
    icon: str  # — ✓ ! ✗ +  (matches terminal wizard glyphs)
    summary: str


@dataclass
class StepView:
    """A single setup_step rendered for the per-tracker form."""

    index: int
    type_: str  # 'env_var' / 'instructions' / 'fda_check' / 'command_test' / 'oauth'
    label: str
    description: str
    field_name: str | None  # None = no input field (instructions/oauth/fda/command_test)
    current_value: str | None
    secret: bool = False
    settings_url: str | None = None  # FDA deep-link, when applicable


@dataclass
class StepResult:
    status: str  # 'ok' / 'skipped' / 'failed'
    detail: str


_FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


def list_overview(cfg: Config) -> list[TrackerOverview]:
    """All trackers — installed first (with status), then bundled-but-not-installed."""
    installed = _installed_trackers(cfg)
    out: list[TrackerOverview] = []
    for name in installed:
        try:
            manifest = load_manifest(cfg.trackers_dir / name / "manifest.yaml")
            icon = compute_icon(cfg, name)
            summary = _summary_for_icon(icon, read_status(cfg).get(name))
            out.append(
                TrackerOverview(
                    name=name,
                    description=manifest.description,
                    installed=True,
                    icon=icon,
                    summary=summary,
                )
            )
        except Exception as e:  # noqa: BLE001 — broken manifest shouldn't kill the page
            out.append(
                TrackerOverview(
                    name=name,
                    description=f"⚠ broken manifest: {e}",
                    installed=True,
                    icon="⚠",
                    summary="see logs",
                )
            )
    for name in list_bundled():
        if name in installed:
            continue
        description = _bundled_description(name)
        out.append(
            TrackerOverview(
                name=name,
                description=description,
                installed=False,
                icon="+",
                summary="not installed",
            )
        )
    return out


def list_step_views(cfg: Config, manifest: Manifest) -> list[StepView]:
    """Build form-field metadata for the per-tracker setup page."""
    env = read_env(cfg.root / ".env")
    views: list[StepView] = []
    for i, step in enumerate(manifest.setup_steps):
        if isinstance(step, EnvVarStep):
            current = env.get(step.name) or os.environ.get(step.name) or ""
            views.append(
                StepView(
                    index=i,
                    type_="env_var",
                    label=step.prompt,
                    description=f"Saved as {step.name} in <root>/.env"
                    + (" · optional" if step.optional else ""),
                    field_name=step.name,
                    current_value=current,
                    secret=step.secret,
                )
            )
        elif isinstance(step, InstructionsStep):
            views.append(
                StepView(
                    index=i,
                    type_="instructions",
                    label="Read and acknowledge",
                    description=step.text,
                    field_name=f"_ack_{i}",
                    current_value=None,
                )
            )
        elif isinstance(step, FdaCheckStep):
            views.append(
                StepView(
                    index=i,
                    type_="fda_check",
                    label="Full Disk Access required",
                    description=(
                        f"personal-db needs read access to {step.probe_path}. "
                        "Open System Settings, grant FDA to the Python interpreter "
                        "running personal-db, then submit the form to re-check."
                    ),
                    field_name=None,
                    current_value=None,
                    settings_url=_FDA_SETTINGS_URL,
                )
            )
        elif isinstance(step, CommandTestStep):
            views.append(
                StepView(
                    index=i,
                    type_="command_test",
                    label="Command check",
                    description=f"Runs on submit: {' '.join(step.command)}",
                    field_name=None,
                    current_value=None,
                )
            )
        elif isinstance(step, OAuthStep):
            token_path = cfg.state_dir / "oauth" / f"{step.provider}.json"
            already = token_path.exists()
            views.append(
                StepView(
                    index=i,
                    type_="oauth",
                    label=f"OAuth ({step.provider})",
                    description=(
                        "Already configured. Re-authorize via terminal if needed."
                        if already
                        else f"Run `personal-db tracker setup {manifest.name}` in your "
                        "terminal to complete the OAuth flow. Web wizard cannot manage "
                        "OAuth callbacks reliably from inside this same tab."
                    ),
                    field_name=None,
                    current_value=None,
                )
            )
    return views


def process_form(
    cfg: Config, name: str, form: dict[str, str]
) -> tuple[list[StepResult], RunResult]:
    """Walk every setup_step against the submitted form. Always tries every step
    so the user sees full feedback; bails before the test sync if any failed.
    Persists status either way."""
    manifest = load_manifest(cfg.trackers_dir / name / "manifest.yaml")
    env_path = cfg.root / ".env"
    results: list[StepResult] = []

    for i, step in enumerate(manifest.setup_steps):
        results.append(_process_step(i, step, cfg, env_path, form, name))

    failed = [r for r in results if r.status == "failed"]
    if failed:
        detail = "; ".join(r.detail for r in failed)
        write_status(cfg, name, success=False, detail=detail)
        return results, RunResult(success=False, detail=detail)

    try:
        sync_one(cfg, name)
    except Exception as e:  # noqa: BLE001
        write_status(cfg, name, success=False, detail=f"test sync failed: {e}")
        return results, RunResult(success=False, detail=f"test sync failed: {e}")

    write_status(cfg, name, success=True, detail="test sync passed")
    return results, RunResult(success=True, detail="test sync passed")


def _process_step(
    i: int,
    step,
    cfg: Config,
    env_path: Path,
    form: dict[str, str],
    tracker_name: str,
) -> StepResult:
    if isinstance(step, EnvVarStep):
        submitted = form.get(step.name, "").strip()
        existing = read_env(env_path).get(step.name) or os.environ.get(step.name) or ""
        value = submitted or existing
        if not value:
            if step.optional:
                return StepResult("skipped", f"{step.name} left unset (optional)")
            return StepResult("failed", f"{step.name} required")
        if submitted:
            upsert_env(env_path, step.name, submitted)
        os.environ[step.name] = value
        return StepResult("ok", f"{step.name} configured")

    if isinstance(step, InstructionsStep):
        if form.get(f"_ack_{i}"):
            return StepResult("ok", "acknowledged")
        return StepResult("failed", "not acknowledged")

    if isinstance(step, FdaCheckStep):
        r = probe_sqlite_access(Path(step.probe_path).expanduser())
        if r.granted:
            return StepResult("ok", f"FDA granted for {step.probe_path}")
        return StepResult("failed", f"FDA denied: {r.reason}")

    if isinstance(step, CommandTestStep):
        try:
            r = subprocess.run(step.command, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return StepResult("failed", f"timed out: {' '.join(step.command)}")
        except FileNotFoundError as e:
            return StepResult("failed", f"command not found: {e}")
        if r.returncode != step.expect_returncode:
            return StepResult(
                "failed", f"exit {r.returncode} (expected {step.expect_returncode})"
            )
        if step.expect_pattern and not re.search(step.expect_pattern, r.stdout):
            return StepResult("failed", f"pattern mismatch: {step.expect_pattern!r}")
        return StepResult("ok", "command verified")

    if isinstance(step, OAuthStep):
        token_path = cfg.state_dir / "oauth" / f"{step.provider}.json"
        if token_path.exists():
            return StepResult("ok", f"OAuth token present for {step.provider}")
        return StepResult(
            "skipped",
            f"OAuth pending — run `personal-db tracker setup {tracker_name}` in terminal",
        )

    return StepResult("failed", f"unknown step type: {type(step).__name__}")


# --- helpers ----------------------------------------------------------------


def _installed_trackers(cfg: Config) -> list[str]:
    if not cfg.trackers_dir.exists():
        return []
    return sorted(
        d.name
        for d in cfg.trackers_dir.iterdir()
        if d.is_dir() and (d / "manifest.yaml").exists()
    )


def _bundled_description(name: str) -> str:
    pkg = resources.files("personal_db.templates.trackers")
    try:
        text = pkg.joinpath(name, "manifest.yaml").read_text()
        return (yaml.safe_load(text) or {}).get("description", "")
    except (yaml.YAMLError, OSError):
        return ""


def _summary_for_icon(icon: str, status: dict | None) -> str:
    if icon == "—":
        return "no setup needed"
    if icon == "✓":
        return "configured · last test passed"
    if icon == "!":
        return f"configured · {(status or {}).get('detail', 'last test failed')}"
    return "needs setup"
