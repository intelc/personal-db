from __future__ import annotations

import asyncio
import urllib.parse
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket

from personal_db.core.apps import load_named_queries
from personal_db.core.config import Config
from personal_db.services.daemon import auth as _auth
from personal_db.services.daemon.agent_terminal import AgentTerminalManager, attach_terminal_websocket
from personal_db.core.manifest import load_manifest
from personal_db.services.ui.viz import load_dashboard_slugs


def register_agent_routes(
    app: FastAPI,
    cfg: Config,
    *,
    agent_terminals: AgentTerminalManager,
    registry: Callable[[], dict[str, Any]],
    app_registry: Callable[[], dict[str, Any]],
    validate_name: Callable[[str], None],
    verify_same_origin_write: Callable[[Request], None],
    daemon_token: str,
) -> None:
    @app.get("/api/agent/context")
    async def api_agent_context(path: str = "/") -> dict[str, Any]:
        """Structured route metadata for the terminal drawer's startup prompt."""
        parsed = urllib.parse.urlparse(path)
        route = parsed.path or "/"
        reg = registry()
        apps = app_registry()
        base: dict[str, Any] = {
            "path": route,
            "dashboard_api": {
                "health": "/api/health",
                "sync_due": "/api/sync_due",
                "app_query_pattern": "/api/apps/{app}/queries/{query}",
                "app_model_pattern": "/api/apps/{app}/models/{model}",
                "app_action_pattern": "/api/apps/{app}/actions/{action}",
            },
            "trackers": (
                sorted(
                    d.name
                    for d in cfg.trackers_dir.iterdir()
                    if d.is_dir() and (d / "manifest.yaml").exists()
                )
                if cfg.trackers_dir.exists()
                else []
            ),
            "apps": [
                {
                    "name": app_def.name,
                    "title": app_def.manifest.title,
                    "description": app_def.manifest.description,
                    "pages": [{"slug": p.slug, "title": p.title} for p in app_def.manifest.pages],
                }
                for app_def in apps.values()
            ],
        }
        if route == "/":
            slugs = load_dashboard_slugs(cfg, reg)
            base.update(
                {
                    "kind": "dashboard",
                    "visualizations": [
                        {
                            "slug": slug,
                            "name": reg[slug].name,
                            "tracker": reg[slug].tracker,
                            "description": reg[slug].description,
                        }
                        for slug in slugs
                        if slug in reg
                    ],
                }
            )
            return base
        if route.startswith("/v/"):
            slug = route.removeprefix("/v/")
            viz = reg.get(slug)
            if viz is None:
                raise HTTPException(status_code=404, detail=f"unknown viz: {slug}")
            base.update(
                {
                    "kind": "visualization",
                    "visualization": {
                        "slug": viz.slug,
                        "name": viz.name,
                        "tracker": viz.tracker,
                        "description": viz.description,
                    },
                }
            )
            return base
        if route.startswith("/t/"):
            tracker = route.removeprefix("/t/").split("/", 1)[0]
            validate_name(tracker)
            manifest_path = cfg.trackers_dir / tracker / "manifest.yaml"
            if not manifest_path.exists():
                raise HTTPException(status_code=404, detail=f"unknown tracker: {tracker}")
            manifest = load_manifest(manifest_path)
            base.update(
                {
                    "kind": "tracker",
                    "tracker": manifest.model_dump(),
                    "visualizations": [
                        {
                            "slug": v.slug,
                            "name": v.name,
                            "description": v.description,
                        }
                        for v in reg.values()
                        if v.tracker == tracker
                    ],
                }
            )
            return base
        if route.startswith("/a/"):
            parts = [part for part in route.split("/") if part]
            app_name = parts[1] if len(parts) >= 2 else ""
            page_slug = parts[2] if len(parts) >= 3 else None
            validate_name(app_name)
            app_def = apps.get(app_name)
            if app_def is None:
                raise HTTPException(status_code=404, detail=f"unknown app: {app_name}")
            page = (
                app_def.manifest.default_page
                if page_slug is None
                else app_def.manifest.page(page_slug)
            )
            queries = load_named_queries(app_def.root / "queries.sql")
            base.update(
                {
                    "kind": "app",
                    "app": {
                        "name": app_def.name,
                        "title": app_def.manifest.title,
                        "description": app_def.manifest.description,
                        "source": app_def.source,
                        "reads": {
                            "tables": list(app_def.manifest.reads.tables),
                            "models": list(app_def.manifest.reads.models),
                        },
                        "writes": {
                            "tables": list(app_def.manifest.writes.tables),
                            "actions": list(app_def.manifest.writes.actions),
                        },
                        "pages": [{"slug": p.slug, "title": p.title} for p in app_def.manifest.pages],
                        "current_page": (
                            {"slug": page.slug, "title": page.title, "view": page.view}
                            if page is not None
                            else None
                        ),
                        "queries": sorted(queries.keys()),
                    },
                }
            )
            return base
        base["kind"] = "other"
        return base

    @app.get("/api/agent/sessions")
    async def api_agent_sessions() -> dict[str, Any]:
        return {"sessions": agent_terminals.list()}

    @app.post("/api/agent/sessions")
    async def api_agent_session_create(request: Request) -> dict[str, Any]:
        verify_same_origin_write(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="request body must be an object")
        cli_type = "codex" if body.get("cli_type") == "codex" else "claude"
        context = body.get("context")
        if context is not None and not isinstance(context, dict):
            raise HTTPException(status_code=400, detail="context must be an object")
        try:
            cols = int(body.get("cols") or 100)
            rows = int(body.get("rows") or 30)
        except (TypeError, ValueError):
            cols, rows = 100, 30
        session = await asyncio.to_thread(
            agent_terminals.create,
            cli_type=cli_type,
            context=context,
            cols=cols,
            rows=rows,
        )
        return {
            "ok": True,
            "session": {
                "id": session.id,
                "cli_type": session.cli_type,
                "alive": session.alive,
                "created_at": session.created_at,
            },
        }

    @app.delete("/api/agent/sessions/{session_id}")
    async def api_agent_session_delete(session_id: str, request: Request) -> dict[str, Any]:
        verify_same_origin_write(request)
        ok = agent_terminals.terminate(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="agent terminal session not found")
        return {"ok": True, "session_id": session_id}

    @app.websocket("/api/agent/sessions/{session_id}/terminal")
    async def api_agent_terminal_ws(websocket: WebSocket, session_id: str) -> None:
        if not _auth.is_authenticated(websocket, daemon_token):
            # Reject before accept() so the client sees a clean handshake
            # failure rather than an open-then-closed socket.
            await websocket.close(code=4401)
            return
        session = agent_terminals.get(session_id)
        if session is None:
            await websocket.close(code=4404)
            return
        await attach_terminal_websocket(websocket, session)
