"""Declared MCP tool implementation for the life_context tracker.

Registered via manifest.yaml `mcp_tools` and dispatched by the MCP server's
extension-tool registry. The signature is (cfg, arguments) ->
JSON-serializable, per the declared-tool entrypoint contract (see
core/manifest.py's McpToolSpec docstring).

This is a thin adapter: the actual implementation lives in
personal_db.core.log_event.log_life_context (core, not services, so the
daemon's `/log_life_context` form route and the menubar quick-log handlers
can call it directly without going through the MCP dispatch machinery).
"""

from __future__ import annotations

from typing import Any

from personal_db.config import Config
from personal_db.log_event import log_life_context as _log_life_context


def log_life_context(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    return _log_life_context(
        cfg,
        start_date=arguments["start_date"],
        end_date=arguments.get("end_date"),
        state=arguments.get("state"),
        note=arguments.get("note"),
    )
