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

import time as _time
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from personal_db.apps import (
    AppContext,
    AppManifestError,
    apply_app_schema,
    discover_apps,
    load_app_view,
)
from personal_db.config import Config
from personal_db.daemon.agent_terminal import AgentTerminalManager
from personal_db.daemon.routes.agent import register_agent_routes
from personal_db.daemon.routes.actions import register_action_routes
from personal_db.daemon.routes.common import validate_name as _validate_name
from personal_db.daemon.routes.setup import register_setup_routes
from personal_db.daemon.routes.sync import register_sync_routes
from personal_db.mcp_server.tools import log_life_context
from personal_db.ui.viz import discover, list_trackers_with_viz, load_dashboard_slugs

_HERE = Path(__file__).parent.parent / "ui"

_NAV_VISIBLE_LIMIT = 6

_DAEMON_START_TS: float = _time.time()
_WRITE_METHODS = {"POST", "DELETE"}
_ALLOWED_DAEMON_HOSTS = {"127.0.0.1", "localhost", "::1"}


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


def _parse_host_header(value: str) -> tuple[str, int | None]:
    value = value.strip()
    if not value:
        raise ValueError("empty host")
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            raise ValueError("invalid host")
        host = value[1:end].lower()
        rest = value[end + 1 :]
        if not rest:
            return host, None
        if not rest.startswith(":") or not rest[1:].isdigit():
            raise ValueError("invalid host port")
        return host, int(rest[1:])
    if value.count(":") == 1:
        host, port_s = value.rsplit(":", 1)
        if not port_s.isdigit():
            raise ValueError("invalid host port")
        return host.lower(), int(port_s)
    return value.lower(), None


def _is_test_client_request(request: Request) -> bool:
    client = request.scope.get("client")
    return bool(client and client[0] == "testclient")


def _verify_daemon_host(request: Request, *, port: int) -> None:
    host_header = request.headers.get("host")
    if not host_header:
        raise HTTPException(status_code=400, detail="missing host header")
    try:
        host, host_port = _parse_host_header(host_header)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid host header") from exc
    if _is_test_client_request(request) and host == "testserver":
        return
    if host not in _ALLOWED_DAEMON_HOSTS or host_port not in (None, port):
        raise HTTPException(status_code=400, detail="invalid host header")


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


def build_app(cfg: Config, *, port: int = 8765) -> FastAPI:
    app = FastAPI(title="personal_db", openapi_url=None, docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    agent_terminals = AgentTerminalManager(cfg)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @app.middleware("http")
    async def _daemon_request_guard(request: Request, call_next):
        try:
            _verify_daemon_host(request, port=port)
            if request.method.upper() in _WRITE_METHODS:
                _verify_same_origin_write(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

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

    register_agent_routes(
        app,
        cfg,
        agent_terminals=agent_terminals,
        registry=_registry,
        app_registry=_app_registry,
        validate_name=_validate_name,
        verify_same_origin_write=_verify_same_origin_write,
    )

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

    register_sync_routes(app, cfg, started_at=_DAEMON_START_TS)
    register_action_routes(
        app,
        cfg,
        app_registry=_app_registry,
        validate_name=_validate_name,
        verify_same_origin_write=_verify_same_origin_write,
    )

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

    register_setup_routes(
        app,
        cfg,
        templates=templates,
        registry=_registry,
        nav_context=_nav_context,
        validate_name=_validate_name,
    )

    @app.post("/log_life_context")
    async def post_life_context(
        request: Request,
        start_date: str = Form(...),
        end_date: str = Form(""),
        state: str = Form(""),
        note: str = Form(""),
    ):
        _verify_same_origin_write(request)
        log_life_context(
            cfg,
            start_date=start_date,
            end_date=end_date or None,
            state=state or None,
            note=note or None,
        )
        referer = request.headers.get("referer") or "/t/life_context"
        return RedirectResponse(url=referer, status_code=303)

    return app
