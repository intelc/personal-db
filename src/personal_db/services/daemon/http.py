"""FastAPI dashboard for personal_db.

Routes:
  GET  /                          → dashboard (configured viz list)
  GET  /v/<slug>                  → single viz on its own page
  GET  /t/<tracker>               → all viz for one tracker
  GET  /t/<tracker>/data          → read-only data browser (raw tracker tables)
  GET  /viz/<slug>/html           → one viz's rendered fragment (pdb-lazy.js)
  GET  /health                    → sync health: last error/success per tracker
  GET  /setup                     → web wizard overview (tracker list + status)
  GET  /setup/<name>              → per-tracker setup form
  POST /setup/<name>              → process setup form, run test sync
  POST /setup/install/<name>      → install a bundled tracker, redirect to /setup/<name>
  POST /setup/oauth/<name>        → start the web OAuth flow for an OAuth-based tracker
  GET  /setup/finish              → finalize page (status only)
  POST /setup/finish/install-daemon → install launchd daemon, redirect to finish
  POST /setup/mcp/install/<tgt>   → install MCP into one target, redirect to finish
  POST /sync/<tracker>            → manual refresh button on viz pages
  POST /log_life_context          → form target for the life_context diary entry

`/`, `/v/<slug>`, and `/t/<tracker>` default to deferring every viz render —
each block ships a placeholder that pdb-lazy.js fills in via GET
`/viz/<slug>/html` after first paint — and accept `?full=1` to restore the
old fully-synchronous render (used by tests, `<noscript>`, and curl).

All programmatic endpoints live under the versioned `/api/v1/...` prefix
(built as one `APIRouter`, see `_api_router` below); the routes above are
browser-facing HTML/form surfaces and are not versioned. Bare `/api/<rest>`
paths (pre-versioning) still resolve via a 308 redirect to `/api/v1/<rest>`
for one transition cycle — see `_legacy_api_redirect` — remove after next
release. The one exception is the agent-terminal websocket
(`/api/v1/agent/sessions/{id}/terminal`): websockets can't be redirected, so
it only exists at the new path.
"""

from __future__ import annotations

import time as _time
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from personal_db.core.apps import (
    AppContext,
    AppManifestError,
    apply_app_schema,
    discover_apps,
    load_app_view,
)
from personal_db.core.config import Config
from personal_db.core.daemon_token import ensure_token
from personal_db.core.log_event import log_life_context
from personal_db.core.manifest import (
    ManifestError,
    humanize_tracker_name,
    load_manifest,
    permission_label,
    platform_label,
)
from personal_db.services.daemon import auth as _auth
from personal_db.services.daemon.agent_terminal import AgentTerminalManager
from personal_db.services.daemon.otc import OtcStore
from personal_db.services.daemon.routes.agent import register_agent_routes
from personal_db.services.daemon.routes.actions import register_action_routes
from personal_db.services.daemon.routes.auth import register_auth_routes
from personal_db.services.daemon.routes.common import validate_name as _validate_name
from personal_db.services.daemon.routes.dashboard import register_dashboard_routes
from personal_db.services.daemon.routes.data import register_data_routes
from personal_db.services.daemon.routes.setup import register_setup_routes
from personal_db.services.daemon.routes.sync import (
    _app_version,
    _db_user_version,
    register_sync_routes,
)
from personal_db.services.ui.builtin_viz import (
    build_health_page_data,
    repeated_failure_trackers,
)
from personal_db.services.ui.viz import discover, list_trackers_with_viz, load_dashboard_slugs

_HERE = Path(__file__).resolve().parents[2] / "ui"

_DAEMON_START_TS: float = _time.time()
_WRITE_METHODS = {"POST", "PUT", "DELETE"}
_ALLOWED_DAEMON_HOSTS = {"127.0.0.1", "localhost", "::1"}
# remove after next release: methods the legacy /api/{rest} -> /api/v1/{rest}
# redirect covers. Every current /api/... route uses one of these.
_LEGACY_API_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


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


def _tracker_title(cfg: Config, tracker: str) -> str:
    """Display title for a tracker page heading.

    Reads the installed tracker's manifest.yaml (one small file, once per
    page load) for `display_title()`; falls back to the mechanical
    `humanize_tracker_name` if the manifest is missing/unparseable.
    """
    manifest_path = cfg.trackers_dir / tracker / "manifest.yaml"
    if manifest_path.is_file():
        try:
            return load_manifest(manifest_path).display_title()
        except ManifestError:
            pass
    return humanize_tracker_name(tracker)


def _dashboard_edit_panel(reg: dict[str, Any], enabled_slugs: list[str]) -> dict[str, Any]:
    """Server-rendered data for the dashboard's "Edit dashboard" panel.

    Splits every non-`auto` viz into two buckets:
      - `enabled`: currently on the dashboard, in dashboard order. Reordered
        client-side with per-row Up/Down buttons (pdb-dashboard.js) —
        drag-to-reorder is out of scope.
      - `groups`: everything else, grouped by tracker (human titles via
        `humanize_tracker_name`) so a long list of available-but-off viz stays
        scannable. Checking one of these rows moves it to the end of the
        enabled order on Save (see pdb-dashboard.js's row-collection logic).
    Auto-synthesized "recent rows" viz are omitted entirely, matching the
    GET /api/v1/dashboard contract (they're reachable via /t/<tracker> and
    /v/<slug> but don't clutter dashboard config).
    """
    order = {slug: i for i, slug in enumerate(enabled_slugs)}
    non_auto = [v for v in reg.values() if not v.auto]
    enabled = sorted((v for v in non_auto if v.slug in order), key=lambda v: order[v.slug])
    disabled = [v for v in non_auto if v.slug not in order]

    groups: list[dict[str, Any]] = []
    by_tracker: dict[str, dict[str, Any]] = {}
    for v in disabled:
        group = by_tracker.get(v.tracker)
        if group is None:
            title = "General" if v.tracker == "_builtin" else humanize_tracker_name(v.tracker)
            # Key is `viz`, not `items` -- a dict already has a built-in
            # `.items()` method, and Jinja's `group.items` attribute lookup
            # would resolve to that bound method (shadowing a same-named
            # dict key) instead of the value we actually want here.
            group = {"tracker": v.tracker, "title": title, "viz": []}
            by_tracker[v.tracker] = group
            groups.append(group)
        group["viz"].append(v)

    return {"enabled": enabled, "groups": groups}


def _render_viz_fragment(cfg: Config, viz: Any) -> str:
    """Render one viz's body HTML, isolating a failing render as inline markup.

    Backs the standalone fragment route (GET /viz/<slug>/html, fetched by
    pdb-lazy.js to fill in the `data-viz-src` placeholders the dashboard/
    tracker/viz pages leave in place of a synchronous render — see
    `_dashboard_edit_panel`'s neighbors below). Same isolation the three page
    routes have always done inline for their own `?full=1` path, just
    factored out so the fragment route and any future caller share it.
    """
    try:
        return viz.render(cfg)
    except Exception as e:  # noqa: BLE001 — isolate one viz's failure from the rest
        return f'<p class="meta">error rendering {viz.slug}: {e}</p>'


def build_app(cfg: Config, *, port: int = 8765) -> FastAPI:
    app = FastAPI(title="personal_db", openapi_url=None, docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    # Small, pure display-label helpers exposed to every template (setup.html's
    # tracker cards, setup_tracker.html's header badges) so they don't need a
    # context var threaded through every route just to render a platform/
    # permission badge.
    templates.env.globals["platform_label"] = platform_label
    templates.env.globals["permission_label"] = permission_label
    agent_terminals = AgentTerminalManager(cfg)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    # Every programmatic (non-browser-HTML) route is versioned under
    # /api/v1/... via this one router, mounted onto `app` at the end of this
    # function (after every register_*_routes call below has added its
    # routes to it). Mounting last doesn't matter for these routes'
    # resolution — only the legacy-redirect catch-all cares about ordering.
    api_router = APIRouter(prefix="/api/v1")

    # Ensures the token exists before the app starts accepting requests —
    # every route but GET /api/v1/health (plus the narrow /auth bootstrap
    # exceptions in services.daemon.auth.EXEMPT_ROUTES) requires it.
    daemon_token = ensure_token(cfg)
    otc_store = OtcStore()

    @app.middleware("http")
    async def _daemon_request_guard(request: Request, call_next):
        try:
            _verify_daemon_host(request, port=port)
            if not _auth.is_exempt(request.method, request.url.path):
                if not _auth.is_authenticated(request, daemon_token):
                    if _auth.wants_html(request):
                        next_q = urllib.parse.quote(
                            request.url.path + (f"?{request.url.query}" if request.url.query else "")
                        )
                        return RedirectResponse(url=f"/auth?next={next_q}", status_code=303)
                    raise HTTPException(status_code=401, detail="missing or invalid daemon token")
            if request.method.upper() in _WRITE_METHODS:
                _verify_same_origin_write(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    register_auth_routes(
        app,
        api_router,
        token=daemon_token,
        otc_store=otc_store,
        verify_same_origin_write=_verify_same_origin_write,
    )

    def _registry():
        # Re-discover on every request so edits to a tracker's visualizations.py
        # take effect without restarting the server.
        return discover(cfg)

    def _app_registry():
        # Like tracker visualizations, apps are re-discovered per request so
        # local edits to app.yaml/views.py/queries.sql are picked up quickly.
        return discover_apps(cfg)

    def _nav_context(reg, active=None):
        # Sidebar nav data: tracker titles are derived mechanically from the
        # slug (no manifest file read per request -- see humanize_tracker_name),
        # apps reuse the same registry the /a routes build their listing from.
        nav_trackers = [
            {"slug": t, "title": humanize_tracker_name(t)}
            for t in list_trackers_with_viz(reg)
        ]
        try:
            apps = _app_registry()
            nav_apps = [
                {"name": a.name, "title": a.manifest.title}
                for a in sorted(apps.values(), key=lambda a: a.manifest.title)
            ]
        except Exception:
            nav_apps = []
        try:
            nav_failing = repeated_failure_trackers(cfg)
        except Exception:
            nav_failing = []
        return {
            "nav_trackers": nav_trackers,
            "nav_apps": nav_apps,
            "nav_failing": nav_failing,
        }

    register_agent_routes(
        api_router,
        cfg,
        agent_terminals=agent_terminals,
        registry=_registry,
        app_registry=_app_registry,
        validate_name=_validate_name,
        verify_same_origin_write=_verify_same_origin_write,
        daemon_token=daemon_token,
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
            "active": definition.name,
            "app": definition,
            "page": page,
            "app_nav": app_nav,
            "html": html,
        }, html

    register_sync_routes(app, api_router, cfg, started_at=_DAEMON_START_TS)
    register_action_routes(
        api_router,
        cfg,
        app_registry=_app_registry,
        validate_name=_validate_name,
        verify_same_origin_write=_verify_same_origin_write,
    )
    register_dashboard_routes(api_router, cfg, registry=_registry)
    register_data_routes(
        app,
        api_router,
        cfg,
        templates=templates,
        nav_context=_nav_context,
        tracker_title=_tracker_title,
        registry=_registry,
    )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, full: bool = False):
        # DEFAULT: don't render any viz synchronously — each block gets a
        # `data-viz-src` placeholder (viz_pending macro) that pdb-lazy.js
        # fills in concurrently after first paint. The live dashboard can be
        # multiple seconds of blocking sqlite queries rendered inline; this
        # is what gets the page to respond instantly. `?full=1` restores the
        # old fully-synchronous render (tests, noscript, curl).
        reg = _registry()
        slugs = load_dashboard_slugs(cfg, reg)
        rendered = []
        for slug in slugs:
            viz = reg.get(slug)
            if viz is None:
                continue
            html = None
            if full:
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
                "full": full,
                "edit_panel": _dashboard_edit_panel(reg, slugs),
                **_nav_context(reg, active="dashboard"),
            },
        )

    @app.get("/viz/{slug:path}/html", response_class=HTMLResponse)
    async def viz_fragment(slug: str):
        # Fragment endpoint pdb-lazy.js fetches to fill in one `data-viz-src`
        # placeholder. Returns ONLY the rendered viz body (what the page
        # routes used to inline directly) — same per-viz error isolation as
        # the ?full=1 page routes (a failing render is a 200 with inline
        # error markup, not a 500), unknown slug is a 404.
        reg = _registry()
        viz = reg.get(slug)
        if viz is None:
            raise HTTPException(status_code=404, detail=f"unknown viz: {slug}")
        return HTMLResponse(_render_viz_fragment(cfg, viz))

    @app.get("/v/{slug:path}", response_class=HTMLResponse)
    async def viz_page(request: Request, slug: str, full: bool = False):
        reg = _registry()
        viz = reg.get(slug)
        if viz is None:
            raise HTTPException(status_code=404, detail=f"unknown viz: {slug}")
        html = None
        if full:
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
                "full": full,
                **_nav_context(reg, active=viz.tracker),
            },
        )

    @app.get("/t/{tracker}", response_class=HTMLResponse)
    async def tracker_page(request: Request, tracker: str, full: bool = False):
        reg = _registry()
        viz_list = [v for v in reg.values() if v.tracker == tracker]
        if not viz_list:
            raise HTTPException(
                status_code=404,
                detail=f"no visualizations for tracker: {tracker}",
            )
        rendered = []
        for v in viz_list:
            html = None
            if full:
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
                "tracker_title": _tracker_title(cfg, tracker),
                "rendered": rendered,
                "full": full,
                **_nav_context(reg, active=tracker),
            },
        )

    @app.get("/health", response_class=HTMLResponse)
    async def health_page(request: Request):
        reg = _registry()
        data = build_health_page_data(
            cfg,
            uptime_seconds=int(_time.time() - _DAEMON_START_TS),
            app_version=_app_version(),
            db_user_version=_db_user_version(cfg),
        )
        return templates.TemplateResponse(
            request=request,
            name="health.html",
            context={
                "active": "health",
                **data,
                **_nav_context(reg, active="health"),
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

    # Mount every /api/v1/... route registered above. Must happen before the
    # legacy-redirect catch-all below is added: Starlette matches routes in
    # registration order, so a concrete /api/v1/<x> route (added here) wins
    # over the broader /api/{rest:path} pattern (added next) for the same
    # request, and only genuinely-unmatched /api/... paths fall through to it.
    app.include_router(api_router)

    @app.api_route("/api/{rest:path}", methods=_LEGACY_API_METHODS)
    async def _legacy_api_redirect(rest: str, request: Request):
        # remove after next release: transitional 308 for pre-versioning
        # clients still hitting bare /api/<rest> paths. Method-preserving so
        # a POST/DELETE here still 308s to the same method on /api/v1/<rest>
        # (per RFC 7538, unlike 301/302/303). The agent-terminal websocket is
        # the one route this can't cover — see the module docstring.
        if rest == "v1" or rest.startswith("v1/"):
            # An /api/v1/... path that didn't match a real v1 route above is
            # genuinely unknown -- redirecting it to itself would loop.
            raise HTTPException(status_code=404, detail=f"not found: /api/{rest}")
        target = f"/api/v1/{rest}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=308)

    return app
