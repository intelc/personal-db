from __future__ import annotations

import asyncio
import contextlib
import time as _time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from personal_db.core.config import Config
from personal_db.core.db import connection
from personal_db.services.daemon._locks import backfill_locked, sync_due_locked, sync_one_locked
from personal_db.services.daemon.routes.common import validate_name
from personal_db.core.sync import sync_one
from personal_db.services.ui.builtin_viz import repeated_failure_trackers

_APP_VERSION_FALLBACK = "0.0.0-dev"


def _app_version() -> str:
    try:
        return _pkg_version("personal_db")
    except PackageNotFoundError:
        return _APP_VERSION_FALLBACK


def _db_user_version(cfg: Config) -> int:
    if not cfg.db_path.exists():
        return 0
    with connection(cfg.db_path, read_only=True) as con:
        (version,) = con.execute("PRAGMA user_version").fetchone()
    return version


def register_sync_routes(
    app: FastAPI, router: APIRouter, cfg: Config, *, started_at: float
) -> None:
    @app.post("/sync/{tracker}")
    async def post_sync(request: Request, tracker: str):
        """Refresh a single tracker, then redirect back to the submitting page."""
        with contextlib.suppress(Exception):
            sync_one(cfg, tracker)
        referer = request.headers.get("referer") or f"/t/{tracker}"
        return RedirectResponse(url=referer, status_code=303)

    @router.get("/health")
    async def api_health() -> dict[str, Any]:
        from personal_db.core.installer import list_bundled

        installed = []
        if cfg.trackers_dir.exists():
            installed = sorted(
                d.name
                for d in cfg.trackers_dir.iterdir()
                if d.is_dir() and (d / "manifest.yaml").exists()
            )
        return {
            "status": "ok",
            "uptime_seconds": int(_time.time() - started_at),
            "trackers": installed,
            "bundled_available": list_bundled(),
            "app_version": _app_version(),
            "api_version": 1,
            "db_user_version": _db_user_version(cfg),
            "repeated_sync_failures": repeated_failure_trackers(cfg),
        }

    @router.post("/sync/{tracker}")
    async def api_sync_one(tracker: str) -> dict[str, Any]:
        validate_name(tracker)
        if not (cfg.trackers_dir / tracker).is_dir():
            raise HTTPException(status_code=404, detail=f"no such tracker: {tracker}")
        try:
            await asyncio.to_thread(sync_one_locked, cfg, tracker)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"sync failed: {e}") from e
        return {"ok": True, "tracker": tracker}

    @router.post("/sync_due")
    async def api_sync_due() -> dict[str, Any]:
        results = await asyncio.to_thread(sync_due_locked, cfg)
        return {"results": results}

    @router.post("/backfill/{tracker}")
    async def api_backfill(tracker: str, request: Request) -> dict[str, Any]:
        validate_name(tracker)
        if not (cfg.trackers_dir / tracker).is_dir():
            raise HTTPException(status_code=404, detail=f"no such tracker: {tracker}")
        start = request.query_params.get("from")
        end = request.query_params.get("to")
        try:
            await asyncio.to_thread(backfill_locked, cfg, tracker, start, end)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"backfill failed: {e}") from e
        return {"ok": True, "tracker": tracker, "from": start, "to": end}
