"""Discovery of declared MCP tools across installed trackers, apps, and sources.

An extension declares `mcp_tools: [{name, description, entrypoint,
input_schema}]` in its manifest; the MCP server (`services.mcp_server.server`)
discovers them at startup and dispatches calls through
`core.entrypoints.load_entrypoint`. This is how domain-specific tools
(finance_*, log_life_context, spark_email_*) are exposed without the core MCP
surface importing extension internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from personal_db.core.apps import discover_apps
from personal_db.core.config import Config
from personal_db.core.manifest import ManifestError, McpToolSpec, load_manifest
from personal_db.core.sources import discover_sources


@dataclass(frozen=True)
class DeclaredMcpTool:
    extension_kind: str  # "tracker" | "app" | "source"
    extension_name: str
    base_dir: Path
    spec: McpToolSpec


def discover_mcp_tools(cfg: Config) -> list[DeclaredMcpTool]:
    """Discover every declared MCP tool on installed trackers, apps, and sources."""
    out: list[DeclaredMcpTool] = []
    if cfg.trackers_dir.exists():
        for entry in sorted(cfg.trackers_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.yaml"
            if not manifest_path.is_file():
                continue
            try:
                manifest = load_manifest(manifest_path)
            except ManifestError:
                continue
            for spec in manifest.mcp_tools:
                out.append(DeclaredMcpTool("tracker", manifest.name, entry, spec))
    for definition in discover_apps(cfg, include_bundled=False).values():
        for spec in definition.manifest.mcp_tools:
            out.append(DeclaredMcpTool("app", definition.name, definition.root, spec))
    for definition in discover_sources(cfg).values():
        for spec in definition.manifest.mcp_tools:
            out.append(DeclaredMcpTool("source", definition.name, definition.root, spec))
    return out
