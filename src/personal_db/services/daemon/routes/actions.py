"""Dynamic tracker and app API routes."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from personal_db.core.apps import (
    AppContext,
    AppDefinition,
    AppManifestError,
    AppQueryError,
    apply_app_schema,
    load_app_module,
)
from personal_db.core.config import Config
from personal_db.core.entrypoints import load_module_from_file


def register_action_routes(
    app: FastAPI,
    cfg: Config,
    *,
    app_registry: Callable[[], dict[str, AppDefinition]],
    validate_name: Callable[[str], None],
    verify_same_origin_write: Callable[[Request], None],
) -> None:
    @app.post("/api/trackers/{name}/actions/{action}")
    async def tracker_action(name: str, action: str, request: Request) -> dict[str, Any]:
        validate_name(name)
        validate_name(action)
        if action.startswith("_"):
            raise HTTPException(
                status_code=404, detail=f"action '{action}' not found on tracker '{name}'"
            )

        tracker_dir = cfg.trackers_dir / name
        actions_path = tracker_dir / "actions.py"
        if not actions_path.exists():
            raise HTTPException(status_code=404, detail=f"tracker '{name}' has no actions.py")

        try:
            module = load_module_from_file(actions_path, f"_pdb_actions_{name}")
        except Exception as exc:
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
        validate_name(name)
        validate_name(query_name)
        apps = app_registry()
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
        validate_name(name)
        validate_name(model)
        apps = app_registry()
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
        validate_name(name)
        validate_name(action)
        if action.startswith("_"):
            raise HTTPException(
                status_code=404, detail=f"action '{action}' not found on app '{name}'"
            )

        apps = app_registry()
        definition = apps.get(name)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"unknown app: {name}")
        if action not in definition.manifest.writes.actions:
            raise HTTPException(
                status_code=404, detail=f"action '{action}' not declared on app '{name}'"
            )
        verify_same_origin_write(request)
        apply_app_schema(cfg, definition.root)

        actions_path = definition.root / "actions.py"
        if not actions_path.exists():
            raise HTTPException(status_code=404, detail=f"app '{name}' has no actions.py")

        try:
            module = load_module_from_file(actions_path, f"_pdb_app_actions_{name}")
        except Exception as exc:
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
