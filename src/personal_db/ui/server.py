"""FastAPI dashboard for personal_db.

Routes:
  GET  /                          → dashboard (configured viz list)
  GET  /v/<slug>                  → single viz on its own page
  GET  /t/<tracker>               → all viz for one tracker
  GET  /setup                     → web wizard overview (tracker list + status)
  GET  /setup/<name>              → per-tracker setup form
  POST /setup/<name>              → process setup form, run test sync
  POST /setup/install/<name>      → install a bundled tracker, redirect to /setup/<name>
  GET  /setup/finish              → finalize page (installs scheduler, MCP options)
  POST /setup/mcp/install/<tgt>   → install MCP into one target, redirect to finish
  POST /sync/<tracker>            → manual refresh button on viz pages
  POST /log_life_context          → form target for the life_context diary entry
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from personal_db.config import Config
from personal_db.db import apply_tracker_schema, init_db
from personal_db.installer import install_template
from personal_db.manifest import load_manifest
from personal_db.mcp_server.tools import log_life_context
from personal_db.sync import sync_one
from personal_db.ui.setup_runner import list_overview, list_step_views, process_form
from personal_db.ui.viz import discover, list_trackers_with_viz, load_dashboard_slugs
from personal_db.wizard.mcp_setup import _TARGETS as _MCP_TARGETS

_HERE = Path(__file__).parent

_NAV_VISIBLE_LIMIT = 6


def _install_scheduler_safe(cfg: Config) -> str:
    """Install the launchd scheduler. Returns a one-line status string for the
    finalize page. Idempotent (the underlying scheduler.install overwrites the
    plist if it already exists). macOS-only."""
    if sys.platform != "darwin":
        return f"⚠ scheduler is macOS-only (detected {sys.platform}); periodic sync skipped"
    try:
        from personal_db import scheduler

        plist = scheduler.install(cfg.root, 600)
        return f"✓ scheduler installed → {plist} (sync every 10 min)"
    except Exception as e:  # noqa: BLE001
        return f"⚠ scheduler install failed: {e}"


def _split_nav(trackers: list[str], active: str | None,
               limit: int = _NAV_VISIBLE_LIMIT) -> tuple[list[str], list[str]]:
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
            except Exception as e:  # noqa: BLE001 — one broken viz shouldn't kill the page
                html = f'<p class="meta">error rendering {slug}: {e}</p>'
            rendered.append({"viz": viz, "html": html})
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"active": "dashboard", "rendered": rendered,
                     **_nav_context(reg, active="dashboard")},
        )

    @app.get("/v/{slug:path}", response_class=HTMLResponse)
    async def viz_page(request: Request, slug: str):
        reg = _registry()
        viz = reg.get(slug)
        if viz is None:
            raise HTTPException(status_code=404, detail=f"unknown viz: {slug}")
        try:
            html = viz.render(cfg)
        except Exception as e:  # noqa: BLE001
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
        viz_list = sorted(
            (v for v in reg.values() if v.tracker == tracker),
            key=lambda v: v.slug,
        )
        if not viz_list:
            raise HTTPException(
                status_code=404,
                detail=f"no visualizations for tracker: {tracker}",
            )
        rendered = []
        for v in viz_list:
            try:
                html = v.render(cfg)
            except Exception as e:  # noqa: BLE001
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

    @app.post("/sync/{tracker}")
    async def post_sync(request: Request, tracker: str):
        """Refresh a single tracker, then redirect back to wherever the form
        was submitted from. Errors are swallowed (logged elsewhere) so the
        redirect always happens — the user can check Health for failures.

        Blocking by design: most incremental syncs are sub-second; the user
        gets immediate visual feedback (spinner) during the request, then
        sees the freshly-synced data on redirect."""
        try:
            sync_one(cfg, tracker)
        except Exception:  # noqa: BLE001 — surface via logs/health, don't 500
            pass
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

    @app.get("/setup/finish", response_class=HTMLResponse)
    async def setup_finish(request: Request, mcp: str = "", mcp_ok: str = ""):
        """Finalize page: scheduler install + MCP target list + dashboard link.

        Side effect: installs the launchd scheduler on every GET (idempotent).
        macOS-only — on Linux/WSL the install is skipped with a notice.

        Registered BEFORE /setup/{name} so `finish` doesn't get matched as a
        tracker name parameter."""
        scheduler_msg = _install_scheduler_safe(cfg)
        reg = _registry()
        targets = [
            {"key": key, "label": tgt.label}
            for key, tgt in _MCP_TARGETS.items()
        ]
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

    return app
