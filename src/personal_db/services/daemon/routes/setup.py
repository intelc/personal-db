"""Setup wizard routes for the daemon UI."""

from __future__ import annotations

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
from personal_db.core.installer import install_template
from personal_db.core.manifest import OAuthStep, load_manifest
from personal_db.core.oauth import ensure_adapter_from_manifest, start_web_oauth
from personal_db.services.ui.setup_runner import list_overview, list_step_views, process_form
from personal_db.services.wizard.env_file import read_env
from personal_db.services.wizard.mcp_setup import _TARGETS as _MCP_TARGETS


def _install_daemon_safe(cfg: Config) -> str:
    """Install the launchd daemon plist and return a one-line status."""
    if (
        os.environ.get("PERSONAL_DB_NO_DAEMON") == "1"
        or os.environ.get("PERSONAL_DB_NO_SCHEDULER") == "1"
    ):
        return "✓ daemon skipped (PERSONAL_DB_NO_DAEMON=1)"
    if sys.platform != "darwin":
        return f"⚠ daemon is macOS-only (detected {sys.platform}); periodic sync skipped"
    try:
        from personal_db.services.daemon import install as di

        result = di.install(cfg.root)
        return f"✓ daemon installed → {result['plist']} (long-running, KeepAlive)"
    except Exception as e:
        return f"⚠ daemon install failed: {e}"


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
    async def setup_overview(request: Request):
        reg = registry()
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={
                "active": "setup",
                "trackers": list_overview(cfg),
                **nav_context(reg, None),
            },
        )

    @app.post("/setup/install/{name}")
    async def setup_install(name: str):
        try:
            dest = install_template(cfg, name)
            init_db(cfg.db_path)
            apply_tracker_schema(cfg.db_path, (dest / "schema.sql").read_text())
        except (FileExistsError, ValueError):
            # Already installed or unknown -- the per-tracker page handles the final state.
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
            msg = f"could not bind callback port {step.redirect_port}: {e}"
            return RedirectResponse(
                url=f"/setup/{name}?msg={urllib.parse.quote(msg)}",
                status_code=303,
            )
        return RedirectResponse(url=auth_url, status_code=303)

    @app.get("/setup/finish", response_class=HTMLResponse)
    async def setup_finish(request: Request, mcp: str = "", mcp_ok: str = ""):
        scheduler_msg = _install_daemon_safe(cfg)
        reg = registry()
        targets = [{"key": key, "label": tgt.label} for key, tgt in _MCP_TARGETS.items()]
        return templates.TemplateResponse(
            request=request,
            name="setup_finish.html",
            context={
                "active": "setup",
                "scheduler_msg": scheduler_msg,
                "mcp_targets": targets,
                "mcp_flash": {"target": mcp, "ok": mcp_ok == "1"} if mcp else None,
                **nav_context(reg, None),
            },
        )

    @app.post("/setup/mcp/install/{target}")
    async def setup_mcp_install(target: str):
        if target not in _MCP_TARGETS:
            raise HTTPException(status_code=404, detail=f"unknown MCP target: {target}")
        ok, _detail = _MCP_TARGETS[target].auto()
        return RedirectResponse(
            url=f"/setup/finish?mcp={target}&mcp_ok={'1' if ok else '0'}",
            status_code=303,
        )

    @app.get("/setup/{name}", response_class=HTMLResponse)
    async def setup_tracker_get(request: Request, name: str, msg: str = ""):
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
                "manifest": manifest,
                "steps": list_step_views(cfg, manifest),
                "step_results": None,
                "run_result": None,
                "flash": msg,
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
                "manifest": manifest,
                "steps": list_step_views(cfg, manifest),
                "step_results": results,
                "run_result": run_result,
                "flash": "",
                **nav_context(reg, None),
            },
        )
