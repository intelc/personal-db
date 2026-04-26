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
import sqlite3
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Callable

import yaml

from personal_db.config import Config
from personal_db.manifest import Manifest, ManifestError, load_manifest

_BUILTIN_TRACKER = "_builtin"
_RECENT_LIMIT = 20
_CELL_TRUNCATE = 100


@dataclass(frozen=True)
class Visualization:
    slug: str  # globally unique: "<tracker>:<short>" (e.g. "daily_time_accounting:today_stack")
    tracker: str  # tracker that owns it, or "_builtin"
    short: str  # the slug suffix
    name: str
    description: str
    render: Callable[[Config], str]
    auto: bool = False  # synthesized by the framework; off by default on dashboard


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
        auto=bool(raw.get("auto", False)),
    )


def _truncate(value, n: int = _CELL_TRUNCATE) -> str:
    if value is None:
        return ""
    s = str(value)
    return s if len(s) <= n else s[: n - 1] + "…"


def _synthesize_recent_viz(name: str, manifest: Manifest) -> Visualization:
    """Default 'recent rows' viz for trackers that don't ship their own viz file.

    Pulls SELECT * FROM <primary_table> ORDER BY <time_column> DESC LIMIT 20
    and renders as an HTML table. Cells truncated; HTML-escaped throughout.
    """
    primary_table = next(iter(manifest.schema.tables))
    time_col = manifest.time_column

    def render(cfg: Config) -> str:
        try:
            con = sqlite3.connect(cfg.db_path)
        except sqlite3.OperationalError:
            return '<p class="meta">database not initialized yet</p>'
        try:
            try:
                count = con.execute(f'SELECT count(*) FROM "{primary_table}"').fetchone()[0]
            except sqlite3.OperationalError:
                return f'<p class="meta">table <code>{escape(primary_table)}</code> not found — run sync</p>'
            if count == 0:
                return f'<p class="meta">no rows in <code>{escape(primary_table)}</code> yet</p>'
            cur = con.execute(
                f'SELECT * FROM "{primary_table}" '
                f'ORDER BY "{time_col}" DESC LIMIT {_RECENT_LIMIT}'
            )
            cols = [c[0] for c in cur.description] if cur.description else []
            rows = cur.fetchall()
            try:
                tmin, tmax = con.execute(
                    f'SELECT MIN("{time_col}"), MAX("{time_col}") FROM "{primary_table}"'
                ).fetchone()
            except sqlite3.OperationalError:
                tmin = tmax = None
        finally:
            con.close()

        header_cells = "".join(f"<th>{escape(c)}</th>" for c in cols)
        body_rows = []
        for r in rows:
            cells = "".join(f"<td>{escape(_truncate(v))}</td>" for v in r)
            body_rows.append(f"<tr>{cells}</tr>")
        meta_bits = [f"{count:,} rows"]
        if tmin and tmax:
            meta_bits.append(f"{escape(str(tmin)[:19])} → {escape(str(tmax)[:19])}")
        meta_line = " · ".join(meta_bits)
        return (
            f'<p class="meta">{meta_line}</p>'
            f'<div class="recent-rows-wrap">'
            f'<table class="recent-rows">'
            f"<thead><tr>{header_cells}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody></table></div>"
            f'<p class="meta">showing latest {min(len(rows), _RECENT_LIMIT)} '
            f"by <code>{escape(time_col)}</code></p>"
        )

    return Visualization(
        slug=f"{name}:recent",
        tracker=name,
        short="recent",
        name="Recent",
        description=f"Latest {_RECENT_LIMIT} rows from {primary_table}, sorted by {time_col}.",
        render=render,
        auto=True,
    )


def _discover_tracker_viz(cfg: Config) -> list[Visualization]:
    """Discover viz for each installed tracker.

    For trackers that ship visualizations.py, load and use those.
    For trackers without one, synthesize a default :recent viz from manifest.
    Either way every installed tracker ends up navigable.
    """
    out: list[Visualization] = []
    if not cfg.trackers_dir.exists():
        return out
    for d in sorted(cfg.trackers_dir.iterdir()):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.yaml"
        viz_path = d / "visualizations.py"
        if not manifest_path.is_file():
            continue

        explicit: list[Visualization] = []
        if viz_path.is_file():
            try:
                mod = _load_module(viz_path, f"personal_db_viz_{d.name}")
                entries = mod.list_visualizations()
                for raw in entries:
                    try:
                        explicit.append(_from_dict(d.name, raw))
                    except (KeyError, TypeError):
                        continue
            except Exception:  # noqa: BLE001 — fall through to synthesized
                explicit = []

        if explicit:
            out.extend(explicit)
        else:
            try:
                manifest = load_manifest(manifest_path)
                out.append(_synthesize_recent_viz(d.name, manifest))
            except ManifestError:
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
    # Default: every curated (non-auto) viz, in registry order. Auto-synthesized
    # "recent rows" viz are still reachable via /t/<tracker> and /v/<slug> but
    # don't clutter the default dashboard.
    return [slug for slug, v in registry.items() if not v.auto]
