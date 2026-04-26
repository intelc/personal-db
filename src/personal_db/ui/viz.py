"""Visualization registry: per-tracker viz declarations + discovery.

Each tracker template that wants to expose dashboard widgets ships a
`visualizations.py` exporting `list_visualizations()` returning a list of
plain dicts (or Visualization instances). The framework loads them at
request time and prefixes each slug with the tracker name.

Built-in cross-cutting viz (health, etc.) live in builtin_viz.py with the
`_builtin` prefix — they don't belong to any single tracker.

Contract:
    visualizations.py:
        def list_visualizations() -> list[dict]:
            return [
                {
                  "slug": "today_stack",  # short, unique within tracker
                  "name": "Today's Time",
                  "description": "Stack of today's hours by category",
                  "render": _render_today_stack,  # callable(cfg) -> str (HTML)
                },
            ]
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from personal_db.config import Config

_BUILTIN_TRACKER = "_builtin"


@dataclass(frozen=True)
class Visualization:
    slug: str  # globally unique: "<tracker>:<short>" (e.g. "daily_time_accounting:today_stack")
    tracker: str  # tracker that owns it, or "_builtin"
    short: str  # the slug suffix
    name: str
    description: str
    render: Callable[[Config], str]


def _load_module(path: Path, modname: str):
    """Load a Python module from a file path. Drop any cached copy first so
    edits to visualizations.py take effect on next request."""
    sys.modules.pop(modname, None)
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _from_dict(tracker: str, raw: dict) -> Visualization:
    short = raw["slug"]
    return Visualization(
        slug=f"{tracker}:{short}",
        tracker=tracker,
        short=short,
        name=raw.get("name", short),
        description=raw.get("description", ""),
        render=raw["render"],
    )


def _discover_tracker_viz(cfg: Config) -> list[Visualization]:
    out: list[Visualization] = []
    if not cfg.trackers_dir.exists():
        return out
    for d in sorted(cfg.trackers_dir.iterdir()):
        if not d.is_dir():
            continue
        viz_path = d / "visualizations.py"
        if not viz_path.is_file():
            continue
        try:
            mod = _load_module(viz_path, f"personal_db_viz_{d.name}")
            entries = mod.list_visualizations()
        except Exception:  # noqa: BLE001 — broken viz files shouldn't crash the dashboard
            continue
        for raw in entries:
            try:
                out.append(_from_dict(d.name, raw))
            except (KeyError, TypeError):
                continue
    return out


def _discover_builtin() -> list[Visualization]:
    """Built-in viz live in personal_db.ui.builtin_viz."""
    from personal_db.ui import builtin_viz

    return [_from_dict(_BUILTIN_TRACKER, raw) for raw in builtin_viz.list_visualizations()]


def discover(cfg: Config) -> dict[str, Visualization]:
    """Build a registry of every available viz, keyed by global slug."""
    registry: dict[str, Visualization] = {}
    for v in _discover_builtin() + _discover_tracker_viz(cfg):
        registry[v.slug] = v
    return registry


def list_trackers_with_viz(registry: dict[str, Visualization]) -> list[str]:
    """Tracker names that have at least one viz, sorted, excluding _builtin."""
    names = {v.tracker for v in registry.values() if v.tracker != _BUILTIN_TRACKER}
    return sorted(names)


# ---------- dashboard config ----------

_CONFIG_REL_PATH = ".config/dashboard.yaml"


def _config_path(cfg: Config) -> Path:
    return cfg.root / _CONFIG_REL_PATH


def load_dashboard_slugs(cfg: Config, registry: dict[str, Visualization]) -> list[str]:
    """Load the user's configured list of slugs to show on the dashboard.

    If <root>/.config/dashboard.yaml is missing or unparseable, falls back to
    "every available slug" (deterministic order: built-in first, then trackers
    alphabetical, viz in declared order). Slugs not present in the registry
    are silently filtered out (e.g. user uninstalled a tracker).
    """
    p = _config_path(cfg)
    if p.exists():
        try:
            data = yaml.safe_load(p.read_text()) or {}
            slugs = data.get("viz") or []
            if isinstance(slugs, list):
                return [s for s in slugs if s in registry]
        except yaml.YAMLError:
            pass
    return list(registry.keys())  # registry is already in deterministic order
