"""Web wizard glue: list tracker statuses, render setup_steps as form fields,
and process submitted form data through the same logical pipeline as the
terminal wizard (`wizard.runner`) — minus the questionary prompts.

OAuth is intentionally NOT executed via web in v0: the redirect dance between
the dashboard's own browser tab and a freshly-spawned local callback server
is fragile, and the terminal flow already works. Web wizard reports OAuth
steps as "needs terminal" and keeps moving.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path

import yaml

from personal_db.services import backfill as backfill_mod
from personal_db.core.config import Config
from personal_db.core.installer import list_bundled
from personal_db.core.manifest import (
    CommandTestStep,
    EnvVarStep,
    FdaCheckStep,
    InstallHooksStep,
    InstructionsStep,
    Manifest,
    NoteStep,
    OAuthStep,
    PlatformName,
    TrackerActionStep,
    VerifyHooksStep,
    humanize_tracker_name,
    load_manifest,
)
from personal_db.core.permissions import probe_sqlite_access
from personal_db.core.sync import sync_one
from personal_db.services.ui.builtin_viz import humanize_age
from personal_db.services.wizard.env_file import read_env, upsert_env
from personal_db.services.wizard.runner import RunResult
from personal_db.services.wizard.status import compute_icon, read_status, write_status


@dataclass
class TrackerOverview:
    name: str
    description: str
    installed: bool
    icon: str  # — ✓ ! ✗ +  (matches terminal wizard glyphs)
    summary: str
    # Additive fields for the human-friendly overview cards (setup.html).
    # `title` falls back to the mechanical `humanize_tracker_name(name)`;
    # `platform`/`permission` mirror the manifest fields verbatim (raw
    # values -- the template applies platform_label()/permission_label()).
    title: str = ""
    platform: list[PlatformName] | None = None
    permission: str = "none"
    # Populated only for installed trackers -- see `_status_chip`.
    status_label: str | None = None
    status_class: str | None = None  # "ok" | "warn"
    last_sync_age: str | None = None  # e.g. "38m ago"


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
    # OAuth-specific (only populated for type_ == 'oauth')
    oauth_index: int | None = None  # nth OAuth step in the manifest (0-based)
    oauth_authorized: bool = False  # token already saved on disk
    oauth_creds_present: bool = False  # client_id_env + client_secret_env both set
    action: str | None = None
    button_label: str | None = None
    status_action: str | None = None
    status_label: str | None = None


@dataclass
class StepResult:
    status: str  # 'ok' / 'skipped' / 'failed'
    detail: str


_FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


def list_overview(cfg: Config) -> list[TrackerOverview]:
    """All trackers — installed first (with status), then bundled-but-not-installed."""
    installed = _installed_trackers(cfg)
    last_runs = _read_last_runs(cfg)
    now = datetime.now(timezone.utc)
    out: list[TrackerOverview] = []
    for name in installed:
        try:
            manifest = load_manifest(cfg.trackers_dir / name / "manifest.yaml")
            icon = compute_icon(cfg, name)
            summary = _summary_for_icon(icon, read_status(cfg).get(name))
            status_label, status_class = _status_chip(icon)
            last_sync_age = None
            ts = last_runs.get(name)
            if ts:
                try:
                    age = now - datetime.fromisoformat(ts)
                    last_sync_age = humanize_age(age)
                    # last_run.json is only written after a fully successful
                    # sync, so a recent entry outranks a stale wizard icon --
                    # "Needs setup" next to "Synced 39s ago" reads as a
                    # contradiction. The per-tracker page still shows the
                    # wizard's step-by-step detail.
                    if status_class == "warn" and age <= timedelta(hours=24):
                        status_label, status_class = "● Ready", "ok"
                except ValueError:
                    last_sync_age = None
            out.append(
                TrackerOverview(
                    name=name,
                    description=manifest.description,
                    installed=True,
                    icon=icon,
                    summary=summary,
                    title=manifest.display_title(),
                    platform=manifest.platform,
                    permission=manifest.permission_type,
                    status_label=status_label,
                    status_class=status_class,
                    last_sync_age=last_sync_age,
                )
            )
        except Exception as e:
            out.append(
                TrackerOverview(
                    name=name,
                    description=f"⚠ broken manifest: {e}",
                    installed=True,
                    icon="⚠",
                    summary="see logs",
                    title=humanize_tracker_name(name),
                )
            )
    for name in list_bundled():
        if name in installed:
            continue
        data = _bundled_manifest_data(name)
        out.append(
            TrackerOverview(
                name=name,
                description=data.get("description", ""),
                installed=False,
                icon="+",
                summary="not installed",
                title=data.get("title") or humanize_tracker_name(name),
                platform=data.get("platform"),
                permission=data.get("permission_type", "none"),
            )
        )
    return out


def list_step_views(cfg: Config, manifest: Manifest) -> list[StepView]:
    """Build form-field metadata for the per-tracker setup page."""
    env = read_env(cfg.root / ".env")
    views: list[StepView] = []
    oauth_counter = 0
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
            cid = env.get(step.client_id_env) or os.environ.get(step.client_id_env) or ""
            cs = (
                env.get(step.client_secret_env)
                or os.environ.get(step.client_secret_env)
                or ""
            )
            creds_present = bool(cid and cs)
            if already:
                description = (
                    f"OAuth token already saved for {step.provider}. "
                    "Re-authorize below if you need a new token."
                )
            elif step.redirect_port is None:
                description = (
                    f"This tracker's manifest doesn't pin a redirect port — run "
                    f"`personal-db tracker setup {manifest.name}` in your terminal "
                    "to complete OAuth. (Web flow needs a fixed pre-registered port.)"
                )
            elif not creds_present:
                description = (
                    f"Set {step.client_id_env} and {step.client_secret_env} above "
                    "and submit once to save them, then click Authorize."
                )
            else:
                description = (
                    f"Click Authorize to open {step.provider} in a new tab. After you "
                    "approve, you'll be sent back to this page."
                )
            views.append(
                StepView(
                    index=i,
                    type_="oauth",
                    label=f"OAuth ({step.provider})",
                    description=description,
                    field_name=None,
                    current_value=None,
                    oauth_index=oauth_counter,
                    oauth_authorized=already,
                    oauth_creds_present=creds_present
                    and step.redirect_port is not None,
                )
            )
            oauth_counter += 1
        elif isinstance(step, InstallHooksStep):
            views.append(
                StepView(
                    index=i,
                    type_="install_hooks",
                    label=step.title,
                    description=step.description or "",
                    field_name=None,
                    current_value=None,
                )
            )
        elif isinstance(step, VerifyHooksStep):
            views.append(
                StepView(
                    index=i,
                    type_="verify_hooks",
                    label=step.title,
                    description="",
                    field_name=None,
                    current_value=None,
                )
            )
        elif isinstance(step, NoteStep):
            views.append(
                StepView(
                    index=i,
                    type_="note",
                    label=step.title,
                    description=step.body,
                    field_name=None,
                    current_value=None,
                )
            )
        elif isinstance(step, TrackerActionStep):
            views.append(
                StepView(
                    index=i,
                    type_="action",
                    label=step.title,
                    description=step.description or "",
                    field_name=None,
                    current_value=None,
                    action=step.action,
                    button_label=step.button_label,
                    status_action=step.status_action,
                    status_label=step.status_label,
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

    # TODO(python_deps): the terminal wizard (wizard/runner.py::run_tracker)
    # auto-installs manifest.python_deps via core.pack_deps.install_tracker_deps
    # before the test sync. The web wizard doesn't do this yet -- wiring it in
    # here needs a StepView-shaped result (new type_ e.g. "python_deps") so
    # setup_tracker.html can render its outcome like every other step, plus a
    # decision on whether the multi-second pip install should block this POST
    # or run as a background step the page polls, which didn't fit in this
    # change's scope. Today: a tracker with python_deps whose ingest.py can't
    # import them will just fail "test sync failed: No module named ..." below
    # (with the CLI-facing `personal-db tracker deps <name>` hint appended by
    # core/sync.py) -- workable but not as smooth as the terminal flow.
    try:
        sync_one(cfg, name)
    except Exception as e:
        write_status(cfg, name, success=False, detail=f"test sync failed: {e}")
        return results, RunResult(success=False, detail=f"test sync failed: {e}")

    write_status(cfg, name, success=True, detail="test sync passed")
    # Detached historical backfill — same as the terminal wizard does.
    backfill_mod.start_async(cfg, name)
    return results, RunResult(success=True, detail="test sync passed; backfill running in background")


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
            f"OAuth pending — click Authorize on this page (or run "
            f"`personal-db tracker setup {tracker_name}` in your terminal)",
        )

    if isinstance(step, (InstallHooksStep, VerifyHooksStep, NoteStep, TrackerActionStep)):
        # These steps are handled client-side (fetch/display) or are informational.
        # They never block the wizard from advancing.
        return StepResult("ok", "n/a")

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


def _bundled_manifest_data(name: str) -> dict:
    """Raw (unvalidated) manifest.yaml dict for a bundled-but-not-installed
    tracker -- used for the overview cards' title/platform/permission/
    description, which don't need full `Manifest` validation."""
    pkg = resources.files("personal_db.templates.trackers")
    try:
        text = pkg.joinpath(name, "manifest.yaml").read_text()
        return yaml.safe_load(text) or {}
    except (yaml.YAMLError, OSError):
        return {}


def _read_last_runs(cfg: Config) -> dict[str, str]:
    p = cfg.state_dir / "last_run.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _status_chip(icon: str) -> tuple[str | None, str | None]:
    """Overview-card status chip for an installed tracker's wizard icon.

    '—' (no setup needed) and '✓' (configured, last test passed) both read
    as ready to use. '✗' (prerequisites missing / never test-synced) and '!'
    (configured but last test sync failed) both need the user's attention.
    """
    if icon in ("—", "✓"):
        return "● Ready", "ok"
    if icon in ("✗", "!"):
        return "Needs setup", "warn"
    return None, None


def _summary_for_icon(icon: str, status: dict | None) -> str:
    if icon == "—":
        return "no setup needed"
    if icon == "✓":
        return "configured · last test passed"
    if icon == "!":
        return f"configured · {(status or {}).get('detail', 'last test failed')}"
    return "needs setup"
