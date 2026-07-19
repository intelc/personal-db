"""Dashboard config API: GET/PUT /api/v1/dashboard.

Backs the "Edit dashboard" panel on the dashboard page (dashboard.html +
pdb-dashboard.js) — lets the UI enable/disable and reorder viz without
hand-editing `<root>/.config/dashboard.yaml`. The yaml shape itself lives in
one place, `services/ui/viz.py` (`load_dashboard_slugs` / `save_dashboard_slugs`)
so this module stays a thin HTTP wrapper.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from personal_db.core.config import Config
from personal_db.services.ui.viz import Visualization, load_dashboard_slugs, save_dashboard_slugs


def register_dashboard_routes(
    router: APIRouter,
    cfg: Config,
    *,
    registry: Callable[[], dict[str, Visualization]],
) -> None:
    @router.get("/dashboard")
    async def api_dashboard_get() -> dict[str, Any]:
        reg = registry()
        slugs = load_dashboard_slugs(cfg, reg)
        order = {slug: i for i, slug in enumerate(slugs)}
        viz = [
            {
                "slug": v.slug,
                "name": v.name,
                "tracker": v.tracker,
                "description": v.description,
                "enabled": v.slug in order,
                "order": order.get(v.slug),
            }
            for v in reg.values()
            if not v.auto
        ]
        return {"viz": viz}

    @router.put("/dashboard")
    async def api_dashboard_put(request: Request) -> dict[str, Any]:
        # Same-origin write verification for PUT happens in the daemon's
        # global request-guard middleware (http.py's _daemon_request_guard,
        # which treats PUT as a write method) — no need to re-check here.
        reg = registry()
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc
        slugs = payload.get("viz") if isinstance(payload, dict) else None
        if not isinstance(slugs, list) or not all(isinstance(s, str) for s in slugs):
            raise HTTPException(status_code=400, detail="'viz' must be a list of slug strings")
        unknown = [s for s in slugs if s not in reg]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown viz slug(s): {', '.join(unknown)}",
            )
        save_dashboard_slugs(cfg, slugs)
        return {"ok": True, "viz": slugs}
