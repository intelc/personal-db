from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from personal_db.core.config import Config
from personal_db.core.manifest import McpToolSpec

_SOURCE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_SOURCE_TEMPLATE_FILES = ("source.yaml", "instructions.md", "tools.py")


class SourceManifestError(ValueError):
    pass


@dataclass(frozen=True)
class SourceManifest:
    name: str
    description: str
    provider: str
    enabled: bool = True
    command: str | None = None
    capabilities: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)
    setup_steps: tuple[dict[str, Any], ...] = ()
    mcp_tools: tuple[McpToolSpec, ...] = ()


@dataclass(frozen=True)
class SourceDefinition:
    name: str
    root: Path
    manifest: SourceManifest
    source: str


def _validate_name(value: str, label: str = "source name") -> None:
    if not _SOURCE_NAME_RE.match(value):
        raise SourceManifestError(f"invalid {label}: {value!r}")


def _strings(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SourceManifestError(f"{field_name} must be a list of strings")
    return tuple(value)


def load_source_manifest(path: Path) -> SourceManifest:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise SourceManifestError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SourceManifestError("source manifest must be a mapping")

    name = raw.get("name")
    if not isinstance(name, str):
        raise SourceManifestError("source manifest requires name")
    _validate_name(name)

    provider = raw.get("provider")
    if not isinstance(provider, str):
        raise SourceManifestError("source manifest requires provider")
    _validate_name(provider, "provider")

    config = raw.get("config") or {}
    if not isinstance(config, dict):
        raise SourceManifestError("config must be a mapping")
    setup_steps = raw.get("setup_steps") or []
    if not isinstance(setup_steps, list) or any(not isinstance(step, dict) for step in setup_steps):
        raise SourceManifestError("setup_steps must be a list of mappings")

    command = raw.get("command")
    if command is not None and not isinstance(command, str):
        raise SourceManifestError("command must be a string")

    mcp_tools_raw = raw.get("mcp_tools")
    if mcp_tools_raw is None:
        mcp_tools: tuple[McpToolSpec, ...] = ()
    else:
        if not isinstance(mcp_tools_raw, list):
            raise SourceManifestError("mcp_tools must be a list")
        try:
            mcp_tools = tuple(McpToolSpec.model_validate(item) for item in mcp_tools_raw)
        except ValidationError as exc:
            raise SourceManifestError(f"invalid mcp_tools: {exc}") from exc

    return SourceManifest(
        name=name,
        description=str(raw.get("description") or ""),
        provider=provider,
        enabled=bool(raw.get("enabled", True)),
        command=command,
        capabilities=_strings(raw.get("capabilities"), field_name="capabilities"),
        config=config,
        setup_steps=tuple(setup_steps),
        mcp_tools=mcp_tools,
    )


def _bundled_sources_root() -> Path | None:
    try:
        pkg = resources.files("personal_db.templates.sources")
    except ModuleNotFoundError:
        return None
    with resources.as_file(pkg) as path:
        return path


def list_bundled_sources() -> list[str]:
    root = _bundled_sources_root()
    if root is None or not root.exists():
        return []
    return sorted(
        entry.name for entry in root.iterdir() if entry.is_dir() and (entry / "source.yaml").is_file()
    )


def install_source_template(cfg: Config, name: str) -> Path:
    dest = cfg.sources_dir / name
    if dest.exists():
        raise FileExistsError(f"already installed: {dest}")
    bundled = _bundled_sources_root()
    src = bundled / name if bundled else None
    if src is None or not src.is_dir():
        raise ValueError(f"unknown built-in source: {name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    return dest


def update_source_template(cfg: Config, name: str) -> Path:
    bundled = _bundled_sources_root()
    src = bundled / name if bundled else None
    if src is None or not src.is_dir():
        raise ValueError(f"unknown built-in source: {name}")
    dest = cfg.sources_dir / name
    dest.mkdir(parents=True, exist_ok=True)
    for fname in _SOURCE_TEMPLATE_FILES:
        src_f = src / fname
        if src_f.is_file():
            (dest / fname).write_bytes(src_f.read_bytes())
    return dest


def discover_sources(cfg: Config, *, include_bundled: bool = False) -> dict[str, SourceDefinition]:
    out: dict[str, SourceDefinition] = {}
    roots: list[tuple[str, Path]] = []
    if include_bundled:
        bundled = _bundled_sources_root()
        if bundled and bundled.exists():
            roots.append(("bundled", bundled))
    if cfg.sources_dir.exists():
        roots.append(("installed", cfg.sources_dir))

    for source_name, root in roots:
        for entry in sorted(root.iterdir()):
            manifest_path = entry / "source.yaml"
            if not entry.is_dir() or not manifest_path.is_file():
                continue
            manifest = load_source_manifest(manifest_path)
            out[manifest.name] = SourceDefinition(
                name=manifest.name,
                root=entry,
                manifest=manifest,
                source=source_name,
            )
    return out


def get_source_definition(
    cfg: Config,
    name: str,
    *,
    include_bundled: bool = False,
) -> SourceDefinition:
    _validate_name(name)
    definition = discover_sources(cfg, include_bundled=include_bundled).get(name)
    if definition is None:
        raise FileNotFoundError(f"source not installed: {name}")
    return definition
