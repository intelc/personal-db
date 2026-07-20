"""Tile gallery API: GET /api/v1/tiles.

Backs the "/" tile gallery (dashboard_tiles.html + pdb-tiles.js) -- one tile
per installed tracker, each rotating through up to 4 headline metrics. See
services/ui/tiles.py for the loader (metrics-contract consumption, fallback
derivation, per-tracker exception isolation, TTL cache) this endpoint is a
thin wrapper over.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from personal_db.core.config import Config
from personal_db.services.ui.tiles import get_tiles


def register_tiles_routes(router: APIRouter, cfg: Config) -> None:
    @router.get("/tiles")
    async def api_tiles() -> dict[str, Any]:
        return {"tiles": get_tiles(cfg)}
