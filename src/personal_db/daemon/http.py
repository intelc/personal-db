"""FastAPI dashboard for personal_db.

Routes:
  GET  /                          → dashboard (configured viz list)
  GET  /v/<slug>                  → single viz on its own page
  GET  /t/<tracker>               → all viz for one tracker
  GET  /setup                     → web wizard overview (tracker list + status)
  GET  /setup/<name>              → per-tracker setup form
  POST /setup/<name>              → process setup form, run test sync
  POST /setup/install/<name>      → install a bundled tracker, redirect to /setup/<name>
  POST /setup/oauth/<name>        → start the web OAuth flow for an OAuth-based tracker
  GET  /setup/finish              → finalize page (installs scheduler, MCP options)
  POST /setup/mcp/install/<tgt>   → install MCP into one target, redirect to finish
  POST /sync/<tracker>            → manual refresh button on viz pages
  POST /log_life_context          → form target for the life_context diary entry
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import time as _time
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from personal_db.apps import (
    AppContext,
    AppManifestError,
    AppQueryError,
    apply_app_schema,
    discover_apps,
    load_app_module,
    load_app_view,
)
from personal_db.config import Config
from personal_db.daemon._locks import backfill_locked, sync_due_locked, sync_one_locked
from personal_db.db import apply_tracker_schema, init_db
from personal_db.installer import install_template
from personal_db.manifest import OAuthStep, load_manifest
from personal_db.mcp_server.tools import log_life_context
from personal_db.oauth import ensure_adapter_from_manifest, start_web_oauth
from personal_db.sync import sync_one
from personal_db.ui.setup_runner import list_overview, list_step_views, process_form
from personal_db.ui.viz import discover, list_trackers_with_viz, load_dashboard_slugs
from personal_db.wizard.env_file import read_env
from personal_db.wizard.mcp_setup import _TARGETS as _MCP_TARGETS

_HERE = Path(__file__).parent.parent / "ui"

_NAV_VISIBLE_LIMIT = 6

_TRACKER_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_DAEMON_START_TS: float = _time.time()


def _validate_name(name: str) -> None:
    if not _TRACKER_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"invalid tracker name: {name!r}")


def _matches_request_origin(value: str, request: Request) -> bool:
    parsed = urllib.parse.urlparse(value)
    if not parsed.scheme and not parsed.netloc:
        return value.startswith("/")
    base = urllib.parse.urlparse(str(request.base_url))
    return (
        parsed.scheme.lower() == base.scheme.lower()
        and parsed.netloc.lower() == base.netloc.lower()
    )


def _verify_same_origin_write(request: Request) -> None:
    """Reject browser-originated writes from other origins.

    Local scripts and tests often omit Origin/Referer entirely, so absence of
    both headers is allowed. Browsers include Origin on normal POSTs; if either
    browser provenance header is present it must point back to this daemon.
    """
    origin = request.headers.get("origin")
    if origin:
        if not _matches_request_origin(origin, request):
            raise HTTPException(status_code=403, detail="cross-origin app action rejected")
        return
    referer = request.headers.get("referer")
    if referer and not _matches_request_origin(referer, request):
        raise HTTPException(status_code=403, detail="cross-origin app action rejected")


def _install_daemon_safe(cfg: Config) -> str:
    """Install the launchd daemon plist. Returns a one-line status string for the
    finalize page. Idempotent. macOS-only.

    Honors PERSONAL_DB_NO_DAEMON=1 (and the deprecated PERSONAL_DB_NO_SCHEDULER=1)
    so tests/demos can opt out of clobbering the user's real install."""
    import os

    if (
        os.environ.get("PERSONAL_DB_NO_DAEMON") == "1"
        or os.environ.get("PERSONAL_DB_NO_SCHEDULER") == "1"
    ):
        return "✓ daemon skipped (PERSONAL_DB_NO_DAEMON=1)"
    if sys.platform != "darwin":
        return f"⚠ daemon is macOS-only (detected {sys.platform}); periodic sync skipped"
    try:
        from personal_db.daemon import install as di

        result = di.install(cfg.root)
        return f"✓ daemon installed → {result['plist']} (long-running, KeepAlive)"
    except Exception as e:
        return f"⚠ daemon install failed: {e}"


def _split_nav(
    trackers: list[str], active: str | None, limit: int = _NAV_VISIBLE_LIMIT
) -> tuple[list[str], list[str]]:
    """Cap inline nav at `limit`; remainder goes into a dropdown.

    If the active tracker would otherwise be hidden in the dropdown, swap it
    into the last visible slot so the highlighted tab always shows. The
    displaced tracker bumps into the dropdown (sorted) so behavior stays
    deterministic across page loads.
    """
    if len(trackers) <= limit:
        return list(trackers), []
    visible = list(trackers[:limit])
    overflow = list(trackers[limit:])
    if active and active in overflow:
        displaced = visible[-1]
        visible[-1] = active
        overflow.remove(active)
        overflow.append(displaced)
        overflow.sort()
    return visible, overflow


def build_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="personal_db", openapi_url=None, docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    def _registry():
        # Re-discover on every request so edits to a tracker's visualizations.py
        # take effect without restarting the server.
        return discover(cfg)

    def _nav_context(reg, active=None):
        visible, overflow = _split_nav(list_trackers_with_viz(reg), active)
        return {"nav_visible": visible, "nav_overflow": overflow}

    def _app_registry():
        # Like tracker visualizations, apps are re-discovered per request so
        # local edits to app.yaml/views.py/queries.sql are picked up quickly.
        return discover_apps(cfg)

    def _render_app_page(app_name: str, page_slug: str | None = None) -> tuple[dict[str, Any], str]:
        _validate_name(app_name)
        apps = _app_registry()
        definition = apps.get(app_name)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"unknown app: {app_name}")
        page = (
            definition.manifest.default_page
            if page_slug is None
            else definition.manifest.page(page_slug)
        )
        if page is None:
            raise HTTPException(status_code=404, detail=f"unknown app page: {app_name}/{page_slug}")
        try:
            apply_app_schema(cfg, definition.root)
            view = load_app_view(definition, page)
            ctx = AppContext(cfg=cfg, app_dir=definition.root, manifest=definition.manifest)
            html = view(ctx)
        except AppManifestError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"error rendering app page: {e}") from e
        app_nav = [
            {
                "href": f"/a/{definition.name}/{p.slug}",
                "slug": p.slug,
                "title": p.title,
                "active": p.slug == page.slug,
            }
            for p in definition.manifest.pages
        ]
        return {
            "active": "apps",
            "app": definition,
            "page": page,
            "app_nav": app_nav,
            "html": html,
        }, html

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        reg = _registry()
        slugs = load_dashboard_slugs(cfg, reg)
        rendered = []
        for slug in slugs:
            viz = reg.get(slug)
            if viz is None:
                continue
            try:
                html = viz.render(cfg)
            except Exception as e:
                html = f'<p class="meta">error rendering {slug}: {e}</p>'
            rendered.append({"viz": viz, "html": html})
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "active": "dashboard",
                "rendered": rendered,
                **_nav_context(reg, active="dashboard"),
            },
        )

    @app.get("/v/{slug:path}", response_class=HTMLResponse)
    async def viz_page(request: Request, slug: str):
        reg = _registry()
        viz = reg.get(slug)
        if viz is None:
            raise HTTPException(status_code=404, detail=f"unknown viz: {slug}")
        try:
            html = viz.render(cfg)
        except Exception as e:
            html = f'<p class="meta">error rendering: {e}</p>'
        return templates.TemplateResponse(
            request=request,
            name="viz_page.html",
            context={
                "active": viz.tracker,
                "viz": viz,
                "html": html,
                **_nav_context(reg, active=viz.tracker),
            },
        )

    @app.get("/t/{tracker}", response_class=HTMLResponse)
    async def tracker_page(request: Request, tracker: str):
        reg = _registry()
        viz_list = [v for v in reg.values() if v.tracker == tracker]
        if not viz_list:
            raise HTTPException(
                status_code=404,
                detail=f"no visualizations for tracker: {tracker}",
            )
        rendered = []
        for v in viz_list:
            try:
                html = v.render(cfg)
            except Exception as e:
                html = f'<p class="meta">error: {e}</p>'
            rendered.append({"viz": v, "html": html})
        return templates.TemplateResponse(
            request=request,
            name="tracker_page.html",
            context={
                "active": tracker,
                "tracker": tracker,
                "rendered": rendered,
                **_nav_context(reg, active=tracker),
            },
        )

    @app.get("/a", response_class=HTMLResponse)
    async def apps_index(request: Request):
        reg = _registry()
        apps = _app_registry()
        return templates.TemplateResponse(
            request=request,
            name="apps_index.html",
            context={
                "active": "apps",
                "apps": list(apps.values()),
                **_nav_context(reg, active=None),
            },
        )

    @app.get("/a/{app_name}", response_class=HTMLResponse)
    async def app_default_page(request: Request, app_name: str):
        reg = _registry()
        context, _html = _render_app_page(app_name)
        return templates.TemplateResponse(
            request=request,
            name="app_page.html",
            context={**context, **_nav_context(reg, active=None)},
        )

    @app.get("/a/{app_name}/{page_slug}", response_class=HTMLResponse)
    async def app_named_page(request: Request, app_name: str, page_slug: str):
        reg = _registry()
        _validate_name(page_slug)
        context, _html = _render_app_page(app_name, page_slug)
        return templates.TemplateResponse(
            request=request,
            name="app_page.html",
            context={**context, **_nav_context(reg, active=None)},
        )

    @app.post("/sync/{tracker}")
    async def post_sync(request: Request, tracker: str):
        """Refresh a single tracker, then redirect back to wherever the form
        was submitted from. Errors are swallowed (logged elsewhere) so the
        redirect always happens — the user can check Health for failures.

        Blocking by design: most incremental syncs are sub-second; the user
        gets immediate visual feedback (spinner) during the request, then
        sees the freshly-synced data on redirect."""
        with contextlib.suppress(Exception):
            sync_one(cfg, tracker)
        referer = request.headers.get("referer") or f"/t/{tracker}"
        return RedirectResponse(url=referer, status_code=303)

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_overview(request: Request):
        reg = _registry()
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={
                "active": "setup",
                "trackers": list_overview(cfg),
                **_nav_context(reg, active=None),
            },
        )

    @app.post("/setup/install/{name}")
    async def setup_install(name: str):
        try:
            dest = install_template(cfg, name)
            init_db(cfg.db_path)
            apply_tracker_schema(cfg.db_path, (dest / "schema.sql").read_text())
        except (FileExistsError, ValueError):
            # Already installed or unknown — fall through to the per-tracker page,
            # which will render the existing state or 404.
            pass
        return RedirectResponse(url=f"/setup/{name}", status_code=303)

    @app.post("/setup/oauth/{name}")
    async def setup_oauth_start(request: Request, name: str):
        """Start the in-browser OAuth flow for an OAuth-based tracker.

        Spawns a one-shot localhost callback server on the manifest's
        redirect_port and 303-redirects the user to the provider's authorize
        URL. After the provider redirects back to localhost:<redirect_port>,
        the callback server exchanges the code, saves the token, and
        302-redirects the user to /setup/{name}?msg=oauth_completed.
        """
        _validate_name(name)
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

        # Register the tracker's TokenAdapter (if any) before any token op.
        ensure_adapter_from_manifest(cfg.trackers_dir / name, step)

        # Pick up creds from the live env first, then the .env file (the form
        # submission for env_var steps writes there + sets os.environ, but the
        # daemon may have started before any of that).
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

        # Build absolute success_redirect — the callback server runs on
        # redirect_port (≠ daemon port), so a relative URL would land on the
        # wrong host.
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
        """Finalize page: scheduler install + MCP target list + dashboard link.

        Side effect: installs the launchd scheduler on every GET (idempotent).
        macOS-only — on Linux/WSL the install is skipped with a notice.

        Registered BEFORE /setup/{name} so `finish` doesn't get matched as a
        tracker name parameter."""
        scheduler_msg = _install_daemon_safe(cfg)
        reg = _registry()
        targets = [{"key": key, "label": tgt.label} for key, tgt in _MCP_TARGETS.items()]
        return templates.TemplateResponse(
            request=request,
            name="setup_finish.html",
            context={
                "active": "setup",
                "scheduler_msg": scheduler_msg,
                "mcp_targets": targets,
                "mcp_flash": {"target": mcp, "ok": mcp_ok == "1"} if mcp else None,
                **_nav_context(reg, active=None),
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
        reg = _registry()
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
                **_nav_context(reg, active=None),
            },
        )

    @app.post("/setup/{name}", response_class=HTMLResponse)
    async def setup_tracker_post(request: Request, name: str):
        reg = _registry()
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
                **_nav_context(reg, active=None),
            },
        )

    @app.post("/log_life_context")
    async def post_life_context(
        start_date: str = Form(...),
        end_date: str = Form(""),
        state: str = Form(""),
        note: str = Form(""),
    ):
        log_life_context(
            cfg,
            start_date=start_date,
            end_date=end_date or None,
            state=state or None,
            note=note or None,
        )
        # Send the user back where they came from if a referer is set; else /
        return RedirectResponse(url="/", status_code=303)

    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        from personal_db.installer import list_bundled

        installed = []
        if cfg.trackers_dir.exists():
            installed = sorted(
                d.name
                for d in cfg.trackers_dir.iterdir()
                if d.is_dir() and (d / "manifest.yaml").exists()
            )
        return {
            "status": "ok",
            "uptime_seconds": int(_time.time() - _DAEMON_START_TS),
            "trackers": installed,
            "bundled_available": list_bundled(),
        }

    @app.post("/api/sync/{tracker}")
    async def api_sync_one(tracker: str) -> dict[str, Any]:
        _validate_name(tracker)
        if not (cfg.trackers_dir / tracker).is_dir():
            raise HTTPException(status_code=404, detail=f"no such tracker: {tracker}")
        try:
            await asyncio.to_thread(sync_one_locked, cfg, tracker)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"sync failed: {e}") from e
        return {"ok": True, "tracker": tracker}

    @app.post("/api/sync_due")
    async def api_sync_due() -> dict[str, Any]:
        results = await asyncio.to_thread(sync_due_locked, cfg)
        return {"results": results}

    @app.post("/api/backfill/{tracker}")
    async def api_backfill(tracker: str, request: Request) -> dict[str, Any]:
        _validate_name(tracker)
        if not (cfg.trackers_dir / tracker).is_dir():
            raise HTTPException(status_code=404, detail=f"no such tracker: {tracker}")
        start = request.query_params.get("from")
        end = request.query_params.get("to")
        try:
            await asyncio.to_thread(backfill_locked, cfg, tracker, start, end)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"backfill failed: {e}") from e
        return {"ok": True, "tracker": tracker, "from": start, "to": end}

    @app.post("/api/trackers/{name}/actions/{action}")
    async def tracker_action(name: str, action: str, request: Request) -> dict[str, Any]:
        import importlib.util
        import inspect
        import sys

        _validate_name(name)
        _validate_name(action)
        if action.startswith("_"):
            raise HTTPException(
                status_code=404, detail=f"action '{action}' not found on tracker '{name}'"
            )

        tracker_dir = cfg.trackers_dir / name
        actions_path = tracker_dir / "actions.py"
        if not actions_path.exists():
            raise HTTPException(status_code=404, detail=f"tracker '{name}' has no actions.py")

        spec_name = f"_pdb_actions_{name}"
        sys.modules.pop(spec_name, None)
        spec = importlib.util.spec_from_file_location(spec_name, actions_path)
        if spec is None or spec.loader is None:
            raise HTTPException(status_code=500, detail="failed to load actions module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec_name] = module  # register before exec so relative imports resolve
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:
            sys.modules.pop(spec_name, None)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        handler = getattr(module, action, None)
        if handler is None or not callable(handler):
            raise HTTPException(
                status_code=404, detail=f"action '{action}' not found on tracker '{name}'"
            )

        try:
            params = inspect.signature(handler).parameters
            payload: dict[str, Any] = {}
            if len(params) >= 2 and request.headers.get("content-length", "0") != "0":
                payload = await request.json()

            if inspect.iscoroutinefunction(handler):
                if len(params) >= 2:
                    return await handler(cfg, payload)
                return await handler(cfg)

            def _call_handler():
                if len(params) >= 2:
                    return handler(cfg, payload)
                return handler(cfg)

            return await asyncio.to_thread(_call_handler)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/apps/{name}/queries/{query_name}")
    async def app_query(name: str, query_name: str, request: Request) -> dict[str, Any]:
        _validate_name(name)
        _validate_name(query_name)
        apps = _app_registry()
        definition = apps.get(name)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"unknown app: {name}")
        apply_app_schema(cfg, definition.root)
        ctx = AppContext(cfg=cfg, app_dir=definition.root, manifest=definition.manifest)
        params = {key: value for key, value in request.query_params.items()}
        try:
            rows = await asyncio.to_thread(lambda: ctx.query(query_name, **params))
        except AppQueryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"app": name, "query": query_name, "params": params, "rows": rows}

    @app.get("/api/apps/{name}/models/{model}")
    async def app_model(name: str, model: str, request: Request) -> Any:
        import inspect

        _validate_name(name)
        _validate_name(model)
        apps = _app_registry()
        definition = apps.get(name)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"unknown app: {name}")
        if model not in definition.manifest.reads.models:
            raise HTTPException(
                status_code=404, detail=f"model '{model}' not declared on app '{name}'"
            )
        apply_app_schema(cfg, definition.root)
        try:
            module = load_app_module(definition.root, definition.name, "models")
            handler = getattr(module, model, None)
            if handler is None or not callable(handler):
                raise HTTPException(
                    status_code=404, detail=f"model '{model}' not found on app '{name}'"
                )
            ctx = AppContext(cfg=cfg, app_dir=definition.root, manifest=definition.manifest)
            params = {key: value for key, value in request.query_params.items()}
            signature = inspect.signature(handler)
            if inspect.iscoroutinefunction(handler):
                if len(signature.parameters) >= 2:
                    return await handler(ctx, params)
                return await handler(ctx)

            def _call_handler():
                if len(signature.parameters) >= 2:
                    return handler(ctx, params)
                return handler(ctx)

            return await asyncio.to_thread(_call_handler)
        except HTTPException:
            raise
        except AppManifestError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/apps/{name}/actions/{action}")
    async def app_action(name: str, action: str, request: Request) -> dict[str, Any]:
        import importlib.util
        import inspect
        import sys

        _validate_name(name)
        _validate_name(action)
        if action.startswith("_"):
            raise HTTPException(
                status_code=404, detail=f"action '{action}' not found on app '{name}'"
            )

        apps = _app_registry()
        definition = apps.get(name)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"unknown app: {name}")
        if action not in definition.manifest.writes.actions:
            raise HTTPException(
                status_code=404, detail=f"action '{action}' not declared on app '{name}'"
            )
        _verify_same_origin_write(request)
        apply_app_schema(cfg, definition.root)

        actions_path = definition.root / "actions.py"
        if not actions_path.exists():
            raise HTTPException(status_code=404, detail=f"app '{name}' has no actions.py")

        spec_name = f"_pdb_app_actions_{name}"
        sys.modules.pop(spec_name, None)
        spec = importlib.util.spec_from_file_location(spec_name, actions_path)
        if spec is None or spec.loader is None:
            raise HTTPException(status_code=500, detail="failed to load actions module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec_name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:
            sys.modules.pop(spec_name, None)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        handler = getattr(module, action, None)
        if handler is None or not callable(handler):
            raise HTTPException(
                status_code=404, detail=f"action '{action}' not found on app '{name}'"
            )

        try:
            ctx = AppContext(cfg=cfg, app_dir=definition.root, manifest=definition.manifest)
            params = inspect.signature(handler).parameters
            payload: dict[str, Any] = {}
            form_post = False
            if len(params) >= 2 and request.headers.get("content-length", "0") != "0":
                content_type = request.headers.get("content-type", "")
                if content_type.startswith("application/json"):
                    payload = await request.json()
                else:
                    form_post = True
                    form = await request.form()
                    payload = {key: str(value) for key, value in form.items()}

            if inspect.iscoroutinefunction(handler):
                if len(params) >= 2:
                    result = await handler(ctx, payload)
                else:
                    result = await handler(ctx)
                if form_post:
                    return RedirectResponse(
                        url=request.headers.get("referer") or f"/a/{name}", status_code=303
                    )
                return result

            def _call_handler():
                if len(params) >= 2:
                    return handler(ctx, payload)
                return handler(ctx)

            result = await asyncio.to_thread(_call_handler)
            if form_post:
                return RedirectResponse(
                    url=request.headers.get("referer") or f"/a/{name}", status_code=303
                )
            return result
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app
