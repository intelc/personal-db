from __future__ import annotations

import asyncio
import urllib.parse
from collections.abc import Callable
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request, WebSocket

from personal_db.core.apps import load_named_queries
from personal_db.core.config import Config
from personal_db.core.manifest import ManifestError, load_manifest
from personal_db.services.daemon import auth as _auth
from personal_db.services.daemon.agent_terminal import (
    AgentTerminalManager,
    attach_terminal_websocket,
)
from personal_db.services.daemon.routes.common import NEW_SLUG_RE as _NEW_SLUG_RE
from personal_db.services.mcp_server import prompts as P


def _require_agent_terminal_enabled(cfg: Config) -> None:
    if not cfg.agent_terminal.enabled:
        raise HTTPException(
            status_code=403,
            detail="agent terminal disabled; set agent_terminal.enabled in config.yaml",
        )


def _set_agent_terminal_enabled(cfg: Config, enabled: bool) -> None:
    """Persist `agent_terminal.enabled` into `<root>/config.yaml`, preserving
    every other key. No in-memory `cfg` mutation is needed: `cfg.agent_terminal`
    (core/config.py) is a computed property that re-reads config.yaml on every
    access rather than caching, so the very next request already sees the new
    value -- this is the same mechanism tests/_agent_terminal_helpers.py relies
    on to flip the gate without rebuilding the FastAPI app."""
    config_path = cfg.root / "config.yaml"
    data: dict[str, Any] = {}
    if config_path.is_file():
        try:
            loaded = yaml.safe_load(config_path.read_text())
        except yaml.YAMLError:
            loaded = None
        if isinstance(loaded, dict):
            data = loaded
    section = data.get("agent_terminal")
    if not isinstance(section, dict):
        section = {}
    section["enabled"] = enabled
    data["agent_terminal"] = section
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False))


def register_agent_routes(
    router: APIRouter,
    cfg: Config,
    *,
    agent_terminals: AgentTerminalManager,
    registry: Callable[[], dict[str, Any]],
    app_registry: Callable[[], dict[str, Any]],
    validate_name: Callable[[str], None],
    verify_same_origin_write: Callable[[Request], None],
    daemon_token: str,
) -> None:
    @router.get("/agent/context")
    async def api_agent_context(path: str = "/") -> dict[str, Any]:
        """Structured route metadata for the terminal drawer's startup prompt."""
        parsed = urllib.parse.urlparse(path)
        route = parsed.path or "/"
        reg = registry()
        apps = app_registry()
        base: dict[str, Any] = {
            "path": route,
            # Not itself gated by agent_terminal.enabled -- the dashboard
            # frontend needs this to decide whether to show the terminal
            # drawer affordance in the first place.
            "agent_terminal_enabled": cfg.agent_terminal.enabled,
            "dashboard_api": {
                "health": "/api/v1/health",
                "sync_due": "/api/v1/sync_due",
                "app_query_pattern": "/api/v1/apps/{app}/queries/{query}",
                "app_model_pattern": "/api/v1/apps/{app}/models/{model}",
                "app_action_pattern": "/api/v1/apps/{app}/actions/{action}",
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
            # "/" is now the tile gallery (services/ui/tiles.py) -- one tile
            # per installed tracker rather than a configured viz list, so
            # there's no per-slug "enabled visualizations" set to report
            # here anymore. `trackers` (set unconditionally above) already
            # covers what an agent needs to know about the gallery's contents.
            base.update({"kind": "dashboard"})
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

    @router.get("/agent/connector-prompt")
    async def api_agent_connector_prompt(slug: str) -> dict[str, Any]:
        """Renders the `create_connector` prompt for one already-scaffolded
        tracker, so the setup UI can hand it to `window.pdbAgent.ask()` (or a
        "copy prompt" button for an external agent/terminal) without needing
        the MCP prompt machinery. NOT gated on agent_terminal.enabled -- it's
        just text, same rationale as GET /agent/context above."""
        if not _NEW_SLUG_RE.match(slug):
            raise HTTPException(status_code=400, detail=f"invalid slug: {slug!r}")
        if not (cfg.trackers_dir / slug).is_dir():
            raise HTTPException(status_code=404, detail=f"unknown tracker: {slug}")
        title: str | None = None
        description: str | None = None
        manifest_path = cfg.trackers_dir / slug / "manifest.yaml"
        if manifest_path.is_file():
            try:
                manifest = load_manifest(manifest_path)
                title = manifest.title
                description = manifest.description
            except ManifestError:
                pass
        prompt = P.build_create_connector_prompt(
            cfg, slug=slug, title=title, description=description
        )
        return {"prompt": prompt}

    @router.post("/settings/agent-terminal")
    async def api_settings_agent_terminal(request: Request) -> dict[str, Any]:
        """Toggle `config.yaml: agent_terminal.enabled` from the setup UI's
        "Enable agent terminal" button. Never touches `auto_approve` -- that
        stays whatever it already was (default off), this route only ever
        writes the `enabled` key."""
        verify_same_origin_write(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict) or "enabled" not in body:
            raise HTTPException(status_code=400, detail="body must include 'enabled': bool")
        enabled = bool(body["enabled"])
        _set_agent_terminal_enabled(cfg, enabled)
        return {"ok": True, "agent_terminal_enabled": enabled}

    @router.get("/agent/sessions")
    async def api_agent_sessions() -> dict[str, Any]:
        _require_agent_terminal_enabled(cfg)
        return {"sessions": agent_terminals.list()}

    @router.post("/agent/sessions")
    async def api_agent_session_create(request: Request) -> dict[str, Any]:
        _require_agent_terminal_enabled(cfg)
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

    @router.delete("/agent/sessions/{session_id}")
    async def api_agent_session_delete(session_id: str, request: Request) -> dict[str, Any]:
        _require_agent_terminal_enabled(cfg)
        verify_same_origin_write(request)
        ok = agent_terminals.terminate(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="agent terminal session not found")
        return {"ok": True, "session_id": session_id}

    @router.websocket("/agent/sessions/{session_id}/terminal")
    async def api_agent_terminal_ws(websocket: WebSocket, session_id: str) -> None:
        if not cfg.agent_terminal.enabled:
            await websocket.close(code=4403)
            return
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
