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
from personal_db.core.manifest import OAuthStep, PlatformUnsupportedError, load_manifest
from personal_db.core.migrations import apply_pending_migrations
from personal_db.core.oauth import ensure_adapter_from_manifest, start_web_oauth
from personal_db.core.runtime_env import is_app_bundle
from personal_db.core.scaffold import apply_manifest_overrides, scaffold_tracker
from personal_db.services.daemon.routes.common import NEW_SLUG_RE as _NEW_SLUG_RE
from personal_db.services.ui.setup_runner import list_overview, list_step_views, process_form
from personal_db.services.wizard.env_file import read_env
from personal_db.services.wizard.mcp_setup import _TARGETS as _MCP_TARGETS


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
        try:
            dest = install_template(cfg, name)
            manifest = load_manifest(dest / "manifest.yaml")
            init_db(cfg.db_path)
            apply_pending_migrations(cfg, name, dest, manifest)
            apply_tracker_schema(cfg.db_path, (dest / "schema.sql").read_text())
        except (FileExistsError, ValueError, PlatformUnsupportedError):
            # Already installed, unknown, or unsupported on this OS -- the
            # per-tracker page handles the final state.
            pass
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

        ensure_adapter_from_manifest(cfg.trackers_dir / name, step)

        env_file = read_env(cfg.root / ".env")
        cid = os.environ.get(step.client_id_env) or env_file.get(step.client_id_env)
        cs = os.environ.get(step.client_secret_env) or env_file.get(step.client_secret_env)
        if not cid or not cs:
            msg = (
                f"Set {step.client_id_env} and {step.client_secret_env} on this "
                "page first, then click Authorize."
            )
            return RedirectResponse(
                url=f"/setup/{name}?msg={urllib.parse.quote(msg)}",
                status_code=303,
            )
        if step.redirect_port is None:
            msg = (
                "This tracker's manifest doesn't pin a redirect port — finish OAuth "
                f"with `personal-db tracker setup {name}` in your terminal."
            )
            return RedirectResponse(
                url=f"/setup/{name}?msg={urllib.parse.quote(msg)}",
                status_code=303,
            )

        success_msg = urllib.parse.quote(
            f"OAuth completed for {step.provider} — click 'save & test sync' to verify."
        )
        success_redirect = f"{str(request.base_url).rstrip('/')}/setup/{name}?msg={success_msg}"
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
            return RedirectResponse(
                url=f"/setup/{name}?msg={urllib.parse.quote(msg)}",
                status_code=303,
            )
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
