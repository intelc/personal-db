"""Web wizard glue: list tracker statuses, render setup_steps as form fields,
and process submitted form data through the same logical pipeline as the
terminal wizard (`wizard.runner`) — minus the questionary prompts.

OAuth IS executed in-browser: `list_step_views` renders an Authorize button
for any `oauth` step whose manifest has both client_id/secret env vars set
and a pinned `redirect_port`. Clicking it posts to the dedicated
`POST /setup/oauth/{name}` route (`daemon/routes/setup.py`), which spawns a
local callback server via `core.oauth.start_web_oauth` and 303-redirects the
browser to the provider's authorize URL; the callback exchanges the code and
saves the token, then bounces back to this page.

`process_form` (the generic per-step form submit handler below) deliberately
*skips* oauth steps — it only checks whether a token is already on disk and
reports "skipped" otherwise, pointing the user at the Authorize button (or
the terminal wizard) rather than trying to drive the OAuth dance itself.

The only case that still falls back to terminal-only is a manifest whose
`oauth` step doesn't pin a `redirect_port` — the web flow needs a fixed,
pre-registered port to hand the provider, so a `None` port can't be started
from the browser. None of the bundled trackers hit this; it only matters for
custom/unpinned trackers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zlib
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
from personal_db.core.sync_backoff import tracker_state
from personal_db.services.ui.builtin_viz import compute_next_sync, humanize_age
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
    next_sync: str | None = None  # e.g. "next in ~2h" -- only for `every` schedules
    # True when the manifest has no periodic schedule (no `every`/`cron`) --
    # e.g. habits, contacts, life_context. These never auto-sync, so the
    # overview card reads "Last entry {age} ago" instead of "Synced {age}
    # ago" and never shows a next-sync ETA (see setup.html).
    manual: bool = False
    # False only for bundled-but-not-installed trackers whose manifest.platform
    # excludes the current OS (see check_platform_supported) -- drives the
    # greyed-out, no-install-button card on /setup/browse. Installed trackers
    # are always True: they couldn't have been installed here otherwise.
    platform_supported: bool = True
    # Compact-row additions (settings-page redesign, /setup only -- browse
    # keeps using the tracker-card badges/description prose). `monogram` and
    # `tint` are pure, deterministic functions of `name` (see
    # `compute_monogram`/`compute_tint` below) so the same source always gets
    # the same tile across processes/restarts -- crucially NOT Python's salted
    # `hash()`, which would make the tint flicker between server runs.
    # `kind` collapses the manifest's permission/setup-step shape down to the
    # single connection-kind label the row badge shows; see `compute_kind`.
    # `logo` is a `/static/logos/<name>.png` URL when a real brand mark is
    # bundled for this source (see `logo_url`); rows fall back to the
    # monogram tile when it's None. Only marks that read well at tile size
    # are bundled -- a missing logo is a choice, not an oversight.
    monogram: str = "??"
    tint: int = 0
    kind: str | None = None
    logo: str | None = None
    # `sync_backoff` pause state (see core/sync_backoff.py). When `paused`,
    # the compact row's timing column shows `paused_failures` instead of the
    # usual sync age/ETA -- an auto-sync that's been paused for repeated
    # failures reporting a stale "Synced 3d ago" would read as healthy when
    # it isn't. The status chip's label is overridden to "Paused" (see
    # `list_overview`) but keeps status_class "warn" so the Needs-attention
    # filter and sidebar count still include it. A tracker merely in backoff
    # (not yet paused) gets no special treatment here -- it keeps whatever
    # chip/timing its wizard status already produced.
    paused: bool = False
    paused_failures: int | None = None


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
    provider: str | None = None  # step.provider, e.g. "oura" -- for template copy
    oauth_index: int | None = None  # nth OAuth step in the manifest (0-based)
    oauth_authorized: bool = False  # token already saved on disk
    oauth_creds_present: bool = False  # client_id_env + client_secret_env both set
    redirect_uri: str | None = None  # computed from redirect_port, when pinned
    action: str | None = None
    button_label: str | None = None
    status_action: str | None = None
    status_label: str | None = None


@dataclass
class StepResult:
    status: str  # 'ok' / 'skipped' / 'failed'
    detail: str


_FDA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

_MONOGRAM_TINT_COUNT = 8


def compute_monogram(title: str) -> str:
    """Deterministic 2-letter monogram for a source's compact-row tile.

    Takes the first two alphabetic characters of `title` (spaces and
    punctuation stripped), e.g. "Calendar" -> "Ca", "Chrome History" -> "Ch"
    (from "ChromeHistory", not "Ch" as two word-initials). First letter
    upper-cased, second lower-cased, regardless of the source string's own
    casing (so e.g. "iMessage" -> "Im", "XHS" -> "Xh") -- the goal is a
    consistent two-glyph tile, not preserving acronym casing.
    """
    letters = [c for c in title if c.isalpha()]
    if not letters:
        return "??"
    if len(letters) == 1:
        return letters[0].upper()
    return letters[0].upper() + letters[1].lower()


def compute_tint(name: str) -> int:
    """Deterministic tint index (0..7) for a source's monogram tile,
    stable across processes and Python versions -- uses zlib.crc32, NOT
    Python's salted built-in `hash()` (which is randomized per-process via
    PYTHONHASHSEED and would make the tile's color flicker on every
    restart)."""
    return zlib.crc32(name.encode("utf-8")) % _MONOGRAM_TINT_COUNT


def compute_kind(manifest: Manifest) -> str | None:
    """Collapse a manifest's permission type + setup steps down to the single
    connection-kind label the compact row's badge shows: "Full Disk Access" /
    "OAuth" / "API key" / "Manual" / None. Order matters -- full_disk_access
    wins outright, then an oauth step, then an api_key permission or a
    *secret* env_var step (a plain, non-secret env_var doesn't imply an API
    key -- see xhs/xhs_saved, whose env_var steps are config, not
    credentials; granola declares `permission_type: api_key` with no secret
    env_var at all, which is why the permission check is needed too).
    "Manual" is reserved for sources with no periodic schedule -- data enters
    only when the user logs it (habits, contacts). Automatic/derived sources
    with no connection to manage (finance, project_time, subscriptions)
    return None: no badge beats a wrong one."""
    if manifest.permission_type == "full_disk_access":
        return "Full Disk Access"
    if any(isinstance(step, OAuthStep) for step in manifest.setup_steps):
        return "OAuth"
    if manifest.permission_type == "api_key" or any(
        isinstance(step, EnvVarStep) and step.secret for step in manifest.setup_steps
    ):
        return "API key"
    if not (manifest.schedule and (manifest.schedule.every or manifest.schedule.cron)):
        return "Manual"
    return None


# Bundled brand marks for the settings rows, served by the daemon's /static
# mount (services/daemon/http.py). Resolved relative to this file so the same
# path works from a repo checkout, a wheel install, and the frozen app payload
# (all keep personal_db/ui/static/ as real files on disk).
_LOGOS_DIR = Path(__file__).resolve().parents[2] / "ui" / "static" / "logos"


def logo_url(name: str) -> str | None:
    """`/static/logos/<name>.png` if a bundled brand mark exists for this
    tracker, else None (row falls back to the monogram tile). Existence is
    checked on every call rather than cached: the list renders once per
    settings-page load, and a stale positive after a deleted asset would 404
    the <img> every load until a restart."""
    if (_LOGOS_DIR / f"{name}.png").is_file():
        return f"/static/logos/{name}.png"
    return None


def oauth_token_present(cfg: Config, step: OAuthStep) -> bool:
    """Whether an OAuth token has already been saved for `step.provider`.

    Shared by `_process_step` (settings-page form submit) and the wizard
    routes (`daemon/routes/setup.py`), which need the exact same check to
    decide whether an oauth step can be treated as "Continue"-able without
    silently letting an unauthorized step through as skipped.
    """
    return (cfg.state_dir / "oauth" / f"{step.provider}.json").exists()


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
            last_run_dt = None
            ts = last_runs.get(name)
            if ts:
                try:
                    last_run_dt = datetime.fromisoformat(ts)
                    age = now - last_run_dt
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
                    last_run_dt = None
            manual = not (
                manifest.schedule and (manifest.schedule.every or manifest.schedule.cron)
            )
            # A broken/needs-attention source's "next in ~X"/"due now" ETA is
            # misleading -- it implies the schedule is on track when it isn't.
            # Only show it once the tracker is actually Ready.
            next_sync = None
            if status_class != "warn" and manifest.schedule and manifest.schedule.every:
                next_sync = compute_next_sync(manifest.schedule, last_run_dt, now)
            paused = False
            paused_failures = None
            backoff_entry = tracker_state(cfg, name)
            if backoff_entry and backoff_entry.get("paused"):
                paused = True
                paused_failures = backoff_entry.get("consecutive_failures")
                status_label, status_class = "Paused", "warn"
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
                    next_sync=next_sync,
                    manual=manual,
                    monogram=compute_monogram(manifest.display_title()),
                    tint=compute_tint(name),
                    kind=compute_kind(manifest),
                    logo=logo_url(name),
                    paused=paused,
                    paused_failures=paused_failures,
                )
            )
        except Exception as e:
            title = humanize_tracker_name(name)
            out.append(
                TrackerOverview(
                    name=name,
                    description=f"⚠ broken manifest: {e}",
                    installed=True,
                    icon="⚠",
                    summary="see logs",
                    title=title,
                    monogram=compute_monogram(title),
                    tint=compute_tint(name),
                )
            )
    for name in list_bundled():
        if name in installed:
            continue
        data = _bundled_manifest_data(name)
        platform = data.get("platform")
        out.append(
            TrackerOverview(
                name=name,
                description=data.get("description", ""),
                installed=False,
                icon="+",
                summary="not installed",
                title=data.get("title") or humanize_tracker_name(name),
                platform=platform,
                permission=data.get("permission_type", "none"),
                platform_supported=platform is None or sys.platform in platform,
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
            redirect_uri = None
            if step.redirect_port is not None:
                redirect_uri = (
                    f"{step.scheme}://{step.redirect_host}:{step.redirect_port}"
                    f"{step.redirect_path}"
                )
            views.append(
                StepView(
                    index=i,
                    type_="oauth",
                    label=f"OAuth ({step.provider})",
                    description=description,
                    field_name=None,
                    current_value=None,
                    provider=step.provider,
                    oauth_index=oauth_counter,
                    oauth_authorized=already,
                    oauth_creds_present=creds_present
                    and step.redirect_port is not None,
                    redirect_uri=redirect_uri,
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

    has_env_step = any(isinstance(step, EnvVarStep) for step in manifest.setup_steps)
    run_result = run_first_sync(cfg, name, saved_prefix=has_env_step)
    return results, run_result


def run_first_sync(cfg: Config, name: str, *, saved_prefix: bool = False) -> RunResult:
    """Run the test sync + kick off the historical backfill for a tracker whose
    setup_steps have already all passed. Shared by `process_form` (one-page
    settings form) and the step-per-page wizard's finish route
    (`POST /setup/{name}/wizard/finish`) so the "run first sync, persist
    status, start backfill" tail isn't duplicated between them.

    `saved_prefix` prepends "Settings saved. " to a failure detail -- set it
    when the caller just persisted env vars/etc. so a test-sync failure
    doesn't read as "nothing was saved" (see `process_form`'s prior inline
    comment, preserved here).

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
    """
    try:
        sync_one(cfg, name)
    except Exception as e:
        prefix = "Settings saved. " if saved_prefix else ""
        detail = f"{prefix}Test sync failed — {e}"
        write_status(cfg, name, success=False, detail=detail)
        return RunResult(success=False, detail=detail)

    write_status(cfg, name, success=True, detail="test sync passed")
    # Detached historical backfill — same as the terminal wizard does.
    backfill_mod.start_async(cfg, name)
    return RunResult(success=True, detail="test sync passed; backfill running in background")


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
        if oauth_token_present(cfg, step):
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
        return "Needs attention", "warn"
    return None, None


def _summary_for_icon(icon: str, status: dict | None) -> str:
    if icon == "—":
        return "no setup needed"
    if icon == "✓":
        return "configured · last test passed"
    if icon == "!":
        return f"configured · {(status or {}).get('detail', 'last test failed')}"
    return "needs setup"
