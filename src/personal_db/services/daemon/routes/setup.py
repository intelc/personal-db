"""Setup wizard routes for the daemon UI."""

from __future__ import annotations

import errno
import os
import sys
import urllib.parse
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from personal_db.core.config import Config
from personal_db.core.db import apply_tracker_schema, init_db
from personal_db.core.global_writes import blocked_reason
from personal_db.core.installer import install_template, list_bundled
from personal_db.core.manifest import (
    EnvVarStep,
    OAuthStep,
    PlatformUnsupportedError,
    load_manifest,
)
from personal_db.core.migrations import apply_pending_migrations
from personal_db.core.oauth import ensure_adapter_from_manifest, start_web_oauth
from personal_db.core.runtime_env import is_app_bundle
from personal_db.core.scaffold import apply_manifest_overrides, scaffold_tracker
from personal_db.services.daemon.routes.common import NEW_SLUG_RE as _NEW_SLUG_RE
from personal_db.services.ui.setup_runner import (
    StepResult,
    _process_step,
    list_overview,
    list_step_views,
    oauth_token_present,
    process_form,
    run_first_sync,
)
from personal_db.services.wizard.env_file import read_env
from personal_db.services.wizard.mcp_setup import _TARGETS as _MCP_TARGETS
from personal_db.services.wizard.status import compute_icon


def _validate_new_slug(cfg: Config, slug: str) -> str | None:
    """Return an error message if `slug` can't be scaffolded, else None."""
    if not slug or not _NEW_SLUG_RE.match(slug):
        return (
            "Slug must start with a lowercase letter and contain only lowercase "
            "letters, digits, and underscores (2-32 characters)."
        )
    if (cfg.trackers_dir / slug).exists():
        return f"A tracker named '{slug}' is already installed."
    if slug in list_bundled():
        return f"'{slug}' collides with a bundled tracker name."
    return None


def _install_daemon_safe(cfg: Config) -> str:
    """Install the launchd daemon plist and return a one-line status."""
    if os.environ.get("PERSONAL_DB_NO_DAEMON") == "1":
        return "✓ daemon skipped (PERSONAL_DB_NO_DAEMON=1)"
    if sys.platform != "darwin":
        return f"⚠ daemon is macOS-only (detected {sys.platform}); periodic sync skipped"
    try:
        from personal_db.services.daemon import install as di

        result = di.install(cfg.root)
        return f"✓ daemon installed → {result['plist']} (long-running, KeepAlive)"
    except Exception as e:
        return f"⚠ daemon install failed: {e}"


def _daemon_status(root) -> dict[str, Any]:
    """Report the current periodic-sync status with NO side effects.

    Returns a dict:
      state: "disabled" | "unsupported" | "app_managed" | "blocked" |
             "launchd_installed" | "not_installed"
      detail: human-readable one-line status (rendered directly on the page)
      legacy_plist: str | None -- only set when state == "app_managed" and a
        legacy LaunchAgent plist is *also* installed (it's redundant and can
        fight the sidecar for port 8765 -- the Finish page offers to remove
        it, see setup_daemon_remove()).

    `app_managed` takes priority over everything else: inside the packaged
    app, the daemon serving this very page already runs
    services/daemon/server.py::start_periodic_sync in-process, regardless of
    what cfg.root is or whether global writes are blocked for it.
    """
    if os.environ.get("PERSONAL_DB_NO_DAEMON") == "1":
        return {
            "state": "disabled",
            "detail": "daemon disabled (PERSONAL_DB_NO_DAEMON=1)",
            "legacy_plist": None,
        }
    if sys.platform != "darwin":
        return {
            "state": "unsupported",
            "detail": f"daemon is macOS-only (detected {sys.platform}); periodic sync unavailable",
            "legacy_plist": None,
        }

    from personal_db.services.daemon import install as di

    if is_app_bundle():
        legacy = di.plist_path()
        return {
            "state": "app_managed",
            "detail": (
                "Periodic sync is active and managed by the PersonalDB app — "
                "the daemon serving this page runs the sync scheduler itself."
            ),
            "legacy_plist": str(legacy) if legacy.exists() else None,
        }

    reason = blocked_reason(root)
    if reason:
        return {"state": "blocked", "detail": f"⚠ {reason}", "legacy_plist": None}

    p = di.plist_path()
    if p.exists():
        return {"state": "launchd_installed", "detail": f"✓ daemon installed → {p}", "legacy_plist": None}
    return {"state": "not_installed", "detail": "not installed yet", "legacy_plist": None}


def _remove_legacy_daemon_safe(cfg: Config) -> str:
    """Remove the legacy LaunchAgent for the app-managed Finish page's
    "remove legacy service" button. Never raises -- always returns a
    one-line status to flash on the page."""
    reason = blocked_reason(cfg.root)
    if reason:
        return f"⚠ {reason}"
    try:
        from personal_db.services.daemon import install as di

        if not di.plist_path().exists():
            return "no legacy background service found"
        di.remove_legacy_daemon()
        return "✓ legacy background service removed"
    except Exception as e:
        return f"⚠ failed to remove legacy background service: {e}"


def register_setup_routes(
    app: FastAPI,
    cfg: Config,
    *,
    templates: Jinja2Templates,
    registry: Callable[[], dict[str, Any]],
    nav_context: Callable[[Any, str | None], dict[str, Any]],
    validate_name: Callable[[str], None],
) -> None:
    @app.get("/setup", response_class=HTMLResponse)
    async def setup_overview(
        request: Request,
        mcp: str = "",
        mcp_ok: str = "",
        mcp_msg: str = "",
        daemon_msg: str = "",
        daemon_ok: str = "",
    ):
        reg = registry()
        targets = [{"key": key, "label": tgt.label} for key, tgt in _MCP_TARGETS.items()]
        daemon_status = _daemon_status(cfg.root)
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={
                "active": "setup",
                "trackers": list_overview(cfg),
                "daemon_status": daemon_status,
                "app_managed": daemon_status["state"] == "app_managed",
                "daemon_flash": {"msg": daemon_msg, "ok": daemon_ok == "1"} if daemon_msg else None,
                "mcp_targets": targets,
                "mcp_flash": {"target": mcp, "ok": mcp_ok == "1", "msg": mcp_msg} if mcp else None,
                **nav_context(reg, None),
            },
        )

    @app.post("/setup/install/{name}")
    async def setup_install(name: str):
        fresh_install = True
        try:
            dest = install_template(cfg, name)
            manifest = load_manifest(dest / "manifest.yaml")
            init_db(cfg.db_path)
            apply_pending_migrations(cfg, name, dest, manifest)
            apply_tracker_schema(cfg.db_path, (dest / "schema.sql").read_text())
        except (FileExistsError, ValueError, PlatformUnsupportedError):
            # Already installed, unknown, or unsupported on this OS -- the
            # per-tracker page handles the final state.
            fresh_install = False
        # A brand-new install has no configuration yet -- send the user into
        # the step-per-page wizard rather than the (denser) settings page.
        # Already-installed / unknown / unsupported all fall back to the
        # settings page exactly as before.
        if fresh_install:
            return RedirectResponse(url=f"/setup/{name}/wizard", status_code=303)
        return RedirectResponse(url=f"/setup/{name}", status_code=303)

    @app.post("/setup/oauth/{name}")
    async def setup_oauth_start(request: Request, name: str):
        """Start the in-browser OAuth flow for an OAuth-based tracker."""
        validate_name(name)
        manifest_path = cfg.trackers_dir / name / "manifest.yaml"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail=f"unknown tracker: {name}")
        manifest = load_manifest(manifest_path)
        oauth_steps = [s for s in manifest.setup_steps if isinstance(s, OAuthStep)]
        if not oauth_steps:
            raise HTTPException(status_code=400, detail="no OAuth step in this tracker")

        form = await request.form()
        try:
            idx = int(str(form.get("step_index", "0")))
        except ValueError:
            idx = 0
        if idx < 0 or idx >= len(oauth_steps):
            raise HTTPException(status_code=400, detail="step_index out of range")
        step = oauth_steps[idx]

        # Optional bounce-back to the step-per-page wizard: the wizard's
        # Authorize button includes a hidden `wizard_step` field (the
        # settings page's button does not, so this is None there and every
        # redirect below falls back to today's /setup/{name}?msg= behavior).
        # Built from cfg-validated `name` (validate_name above) and this
        # parsed int only -- never from a client-supplied path string.
        wizard_step: int | None = None
        raw_wizard_step = form.get("wizard_step")
        if raw_wizard_step not in (None, ""):
            try:
                parsed = int(str(raw_wizard_step))
                if parsed >= 1:
                    wizard_step = parsed
            except ValueError:
                wizard_step = None

        def _bounce_path(msg: str) -> str:
            q = urllib.parse.quote(msg)
            if wizard_step is not None:
                return f"/setup/{name}/wizard/{wizard_step}?msg={q}"
            return f"/setup/{name}?msg={q}"

        ensure_adapter_from_manifest(cfg.trackers_dir / name, step)

        env_file = read_env(cfg.root / ".env")
        cid = os.environ.get(step.client_id_env) or env_file.get(step.client_id_env)
        cs = os.environ.get(step.client_secret_env) or env_file.get(step.client_secret_env)
        if not cid or not cs:
            msg = (
                f"Set {step.client_id_env} and {step.client_secret_env} on this "
                "page first, then click Authorize."
            )
            return RedirectResponse(url=_bounce_path(msg), status_code=303)
        if step.redirect_port is None:
            msg = (
                "This tracker's manifest doesn't pin a redirect port — finish OAuth "
                f"with `personal-db tracker setup {name}` in your terminal."
            )
            return RedirectResponse(url=_bounce_path(msg), status_code=303)

        if wizard_step is not None:
            success_msg = f"OAuth completed for {step.provider} — continue to finish setup."
        else:
            success_msg = (
                f"OAuth completed for {step.provider} — click 'save & test sync' to verify."
            )
        success_redirect = f"{str(request.base_url).rstrip('/')}{_bounce_path(success_msg)}"
        try:
            auth_url = start_web_oauth(
                cfg,
                provider=step.provider,
                auth_url=step.auth_url,
                token_url=step.token_url,
                client_id=cid,
                client_secret=cs,
                redirect_host=step.redirect_host,
                redirect_port=step.redirect_port,
                redirect_path=step.redirect_path,
                scopes=step.scopes,
                success_redirect=success_redirect,
                scheme=step.scheme,
                scope_separator=step.scope_separator,
            )
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                msg = str(e)
            else:
                msg = f"could not bind callback port {step.redirect_port}: {e}"
            return RedirectResponse(url=_bounce_path(msg), status_code=303)
        return RedirectResponse(url=auth_url, status_code=303)

    @app.get("/setup/finish")
    async def setup_finish():
        # The finish page's content was folded into /setup itself -- keep this
        # route around (bookmarks, packaged-app links with no URL bar) as a
        # redirect rather than removing it.
        return RedirectResponse(url="/setup", status_code=303)

    @app.post("/setup/finish/install-daemon")
    async def setup_daemon_install():
        msg = _install_daemon_safe(cfg)
        ok = msg.startswith("✓")
        return RedirectResponse(
            url=f"/setup?daemon_ok={'1' if ok else '0'}&daemon_msg={urllib.parse.quote(msg)}",
            status_code=303,
        )

    @app.post("/setup/finish/remove-daemon")
    async def setup_daemon_remove():
        msg = _remove_legacy_daemon_safe(cfg)
        ok = msg.startswith("✓") or msg.startswith("no legacy")
        return RedirectResponse(
            url=f"/setup?daemon_ok={'1' if ok else '0'}&daemon_msg={urllib.parse.quote(msg)}",
            status_code=303,
        )

    @app.post("/setup/mcp/install/{target}")
    async def setup_mcp_install(target: str):
        if target not in _MCP_TARGETS:
            raise HTTPException(status_code=404, detail=f"unknown MCP target: {target}")
        reason = blocked_reason(cfg.root)
        if reason:
            return RedirectResponse(
                url=f"/setup?mcp={target}&mcp_ok=0&mcp_msg={urllib.parse.quote(reason)}",
                status_code=303,
            )
        try:
            ok, _detail = _MCP_TARGETS[target].auto()
        except Exception as e:
            return RedirectResponse(
                url=f"/setup?mcp={target}&mcp_ok=0&mcp_msg={urllib.parse.quote(str(e))}",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/setup?mcp={target}&mcp_ok={'1' if ok else '0'}",
            status_code=303,
        )

    @app.get("/setup/new", response_class=HTMLResponse)
    async def setup_new_get(request: Request):
        reg = registry()
        return templates.TemplateResponse(
            request=request,
            name="setup_new.html",
            context={
                "active": "setup",
                "error": None,
                "form": {},
                **nav_context(reg, None),
            },
        )

    @app.post("/setup/new", response_class=HTMLResponse)
    async def setup_new_post(request: Request):
        reg = registry()
        form = dict(await request.form())
        slug = str(form.get("slug", "")).strip()
        title = str(form.get("title", "")).strip()
        description = str(form.get("description", "")).strip()

        error = _validate_new_slug(cfg, slug)
        if error:
            return templates.TemplateResponse(
                request=request,
                name="setup_new.html",
                context={
                    "active": "setup",
                    "error": error,
                    "form": {"slug": slug, "title": title, "description": description},
                    **nav_context(reg, None),
                },
            )

        dest = scaffold_tracker(cfg, slug)
        apply_manifest_overrides(
            dest / "manifest.yaml", title=title or None, description=description or None
        )
        return RedirectResponse(url=f"/setup/{slug}?created=1", status_code=303)

    @app.get("/setup/browse", response_class=HTMLResponse)
    async def setup_browse(request: Request):
        # Marketplace/catalog view -- every bundled tracker, available ones
        # with an install form and already-installed ones shown with a ✓
        # state (see setup_browse.html). Must be registered before the
        # catch-all GET /setup/{name} below so "browse" doesn't get treated
        # as a tracker name.
        reg = registry()
        return templates.TemplateResponse(
            request=request,
            name="setup_browse.html",
            context={
                "active": "setup",
                "trackers": list_overview(cfg),
                **nav_context(reg, None),
            },
        )

    # --- step-per-page first-run wizard (/setup/{name}/wizard/...) --------
    #
    # Registered before the catch-all GET /setup/{name} below for
    # readability (a reader scanning top-to-bottom sees the more specific
    # routes first). FastAPI/Starlette match on segment count + literal vs.
    # typed-path-param regex, so this ordering isn't load-bearing: none of
    # these routes actually collide with the two-segment /setup/{name}
    # pattern, and the `{i:int}` converter on the numbered-step routes
    # already can't match the literal "finish" segment either way.
    #
    # The one-page /setup/{name} settings form (below) stays as-is for
    # already-configured sources; this is only the guided first-time path.

    @app.get("/setup/{name}/wizard")
    async def setup_wizard_root(name: str):
        manifest_path = cfg.trackers_dir / name / "manifest.yaml"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail=f"unknown tracker: {name}")
        manifest = load_manifest(manifest_path)
        if not manifest.setup_steps:
            return RedirectResponse(url=f"/setup/{name}/wizard/finish", status_code=303)
        return RedirectResponse(url=f"/setup/{name}/wizard/1", status_code=303)

    @app.get("/setup/{name}/wizard/finish", response_class=HTMLResponse)
    async def setup_wizard_finish_get(request: Request, name: str, msg: str = ""):
        reg = registry()
        manifest_path = cfg.trackers_dir / name / "manifest.yaml"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail=f"unknown tracker: {name}")
        manifest = load_manifest(manifest_path)
        return templates.TemplateResponse(
            request=request,
            name="setup_wizard.html",
            context={
                "active": "setup",
                "tracker_name": name,
                "tracker_title": manifest.display_title(),
                "manifest": manifest,
                "steps": list_step_views(cfg, manifest),
                "step": None,
                "step_number": None,
                "total_steps": len(manifest.setup_steps),
                "step_result": None,
                "run_result": None,
                "finish": True,
                "flash": msg,
                **nav_context(reg, None),
            },
        )

    @app.post("/setup/{name}/wizard/finish", response_class=HTMLResponse)
    async def setup_wizard_finish_post(request: Request, name: str):
        reg = registry()
        manifest_path = cfg.trackers_dir / name / "manifest.yaml"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail=f"unknown tracker: {name}")
        manifest = load_manifest(manifest_path)
        has_env_step = any(isinstance(s, EnvVarStep) for s in manifest.setup_steps)
        run_result = run_first_sync(cfg, name, saved_prefix=has_env_step)
        return templates.TemplateResponse(
            request=request,
            name="setup_wizard.html",
            context={
                "active": "setup",
                "tracker_name": name,
                "tracker_title": manifest.display_title(),
                "manifest": manifest,
                "steps": list_step_views(cfg, manifest),
                "step": None,
                "step_number": None,
                "total_steps": len(manifest.setup_steps),
                "step_result": None,
                "run_result": run_result,
                "finish": True,
                "flash": "",
                **nav_context(reg, None),
            },
        )

    @app.get("/setup/{name}/wizard/{i}", response_class=HTMLResponse)
    async def setup_wizard_step_get(request: Request, name: str, i: int, msg: str = ""):
        reg = registry()
        manifest_path = cfg.trackers_dir / name / "manifest.yaml"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail=f"unknown tracker: {name}")
        manifest = load_manifest(manifest_path)
        steps = list_step_views(cfg, manifest)
        if i < 1 or i > len(steps):
            return RedirectResponse(url=f"/setup/{name}/wizard", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="setup_wizard.html",
            context={
                "active": "setup",
                "tracker_name": name,
                "tracker_title": manifest.display_title(),
                "manifest": manifest,
                "steps": steps,
                "step": steps[i - 1],
                "step_number": i,
                "total_steps": len(steps),
                "step_result": None,
                "run_result": None,
                "finish": False,
                "flash": msg,
                **nav_context(reg, None),
            },
        )

    @app.post("/setup/{name}/wizard/{i}", response_class=HTMLResponse)
    async def setup_wizard_step_post(request: Request, name: str, i: int):
        reg = registry()
        manifest_path = cfg.trackers_dir / name / "manifest.yaml"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail=f"unknown tracker: {name}")
        manifest = load_manifest(manifest_path)
        setup_steps = manifest.setup_steps
        if i < 1 or i > len(setup_steps):
            return RedirectResponse(url=f"/setup/{name}/wizard", status_code=303)

        form = dict(await request.form())
        step = setup_steps[i - 1]
        env_path = cfg.root / ".env"
        result = _process_step(i - 1, step, cfg, env_path, form, name)

        # _process_step reports "skipped" for an oauth step with no token on
        # disk yet -- correct for the settings page (it just points at the
        # Authorize button), but a dead end for the wizard: without this
        # override, an unauthorized oauth step would silently let Continue
        # through and the tracker would never actually get configured. The
        # one exception is the terminal-only case (no redirect_port pinned
        # in the manifest) -- the web flow can't drive OAuth there at all,
        # so "skipped" genuinely is the correct end state and Continue must
        # still work.
        if (
            isinstance(step, OAuthStep)
            and step.redirect_port is not None
            and not oauth_token_present(cfg, step)
        ):
            result = StepResult("failed", "Click Authorize above before continuing.")

        if result.status in ("ok", "skipped"):
            if i >= len(setup_steps):
                return RedirectResponse(url=f"/setup/{name}/wizard/finish", status_code=303)
            return RedirectResponse(url=f"/setup/{name}/wizard/{i + 1}", status_code=303)

        steps = list_step_views(cfg, manifest)
        return templates.TemplateResponse(
            request=request,
            name="setup_wizard.html",
            context={
                "active": "setup",
                "tracker_name": name,
                "tracker_title": manifest.display_title(),
                "manifest": manifest,
                "steps": steps,
                "step": steps[i - 1],
                "step_number": i,
                "total_steps": len(steps),
                "step_result": result,
                "run_result": None,
                "finish": False,
                "flash": "",
                **nav_context(reg, None),
            },
        )

    @app.get("/setup/{name}", response_class=HTMLResponse)
    async def setup_tracker_get(request: Request, name: str, msg: str = "", created: str = ""):
        reg = registry()
        manifest_path = cfg.trackers_dir / name / "manifest.yaml"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail=f"unknown tracker: {name}")
        manifest = load_manifest(manifest_path)
        return templates.TemplateResponse(
            request=request,
            name="setup_tracker.html",
            context={
                "active": "setup",
                "tracker_name": name,
                "tracker_title": manifest.display_title(),
                "manifest": manifest,
                "steps": list_step_views(cfg, manifest),
                "step_results": None,
                "run_result": None,
                "flash": msg,
                "just_created": created == "1",
                "tracker_root": str(cfg.trackers_dir / name),
                "agent_terminal_enabled": cfg.agent_terminal.enabled,
                "never_configured": compute_icon(cfg, name) == "✗",
                **nav_context(reg, None),
            },
        )

    @app.post("/setup/{name}", response_class=HTMLResponse)
    async def setup_tracker_post(request: Request, name: str):
        reg = registry()
        manifest_path = cfg.trackers_dir / name / "manifest.yaml"
        if not manifest_path.exists():
            raise HTTPException(status_code=404, detail=f"unknown tracker: {name}")
        form = dict(await request.form())
        results, run_result = process_form(cfg, name, form)
        manifest = load_manifest(manifest_path)
        return templates.TemplateResponse(
            request=request,
            name="setup_tracker.html",
            context={
                "active": "setup",
                "tracker_name": name,
                "tracker_title": manifest.display_title(),
                "manifest": manifest,
                "steps": list_step_views(cfg, manifest),
                "step_results": results,
                "run_result": run_result,
                "flash": "",
                **nav_context(reg, None),
            },
        )
