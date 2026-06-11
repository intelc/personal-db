"""App discovery, manifests, and named-query execution."""

from __future__ import annotations

import importlib.util
import re
import shutil
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from personal_db.config import Config
from personal_db.db import connect

_APP_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_QUERY_MARKER_RE = re.compile(r"^\s*--\s*name:\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*$")
_APP_TEMPLATE_FILES = (
    "app.yaml",
    "schema.sql",
    "queries.sql",
    "views.py",
    "actions.py",
    "models.py",
    "instructions.md",
)


class AppManifestError(ValueError):
    pass


class AppQueryError(ValueError):
    pass


@dataclass(frozen=True)
class AppPage:
    slug: str
    title: str
    view: str


@dataclass(frozen=True)
class AppReads:
    tables: tuple[str, ...] = ()
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppWrites:
    tables: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppManifest:
    name: str
    title: str
    description: str
    pages: tuple[AppPage, ...]
    reads: AppReads = field(default_factory=AppReads)
    writes: AppWrites = field(default_factory=AppWrites)

    @property
    def default_page(self) -> AppPage:
        return self.pages[0]

    def page(self, slug: str) -> AppPage | None:
        return next((page for page in self.pages if page.slug == slug), None)


@dataclass(frozen=True)
class AppDefinition:
    name: str
    root: Path
    manifest: AppManifest
    source: str


@dataclass(frozen=True)
class AppContext:
    cfg: Config
    app_dir: Path
    manifest: AppManifest

    def query(self, name: str, **params: Any) -> list[dict[str, Any]]:
        queries = load_named_queries(self.app_dir / "queries.sql")
        sql = queries.get(name)
        if sql is None:
            raise AppQueryError(f"unknown query: {name}")
        return run_named_query(self.cfg, sql, params)

    def query_url(self, name: str) -> str:
        _validate_identifier(name, "query")
        return f"/api/apps/{self.manifest.name}/queries/{name}"

    def model_url(self, name: str) -> str:
        _validate_identifier(name, "model")
        return f"/api/apps/{self.manifest.name}/models/{name}"

    def action_url(self, name: str) -> str:
        _validate_identifier(name, "action")
        return f"/api/apps/{self.manifest.name}/actions/{name}"

    def require_write_tables(self, *tables: str) -> None:
        """Assert that an app action is allowed to write the named tables."""
        allowed = set(self.manifest.writes.tables)
        missing = [table for table in tables if table not in allowed]
        if missing:
            raise AppManifestError(
                f"app {self.manifest.name} is not allowed to write: {', '.join(missing)}"
            )

    def module(self, stem: str) -> Any:
        _validate_identifier(stem, "module")
        return load_app_module(self.app_dir, self.manifest.name, stem)


def _validate_identifier(value: str, label: str) -> None:
    if not _APP_NAME_RE.match(value):
        raise AppManifestError(f"invalid {label}: {value!r}")


def _strings(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AppManifestError(f"{field_name} must be a list of strings")
    return tuple(value)


def load_app_manifest(path: Path) -> AppManifest:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise AppManifestError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise AppManifestError("app manifest must be a mapping")

    name = raw.get("name")
    if not isinstance(name, str):
        raise AppManifestError("app manifest requires name")
    _validate_identifier(name, "app name")

    pages_raw = raw.get("pages")
    if not isinstance(pages_raw, list) or not pages_raw:
        raise AppManifestError("app manifest requires at least one page")
    pages: list[AppPage] = []
    seen_pages: set[str] = set()
    for entry in pages_raw:
        if not isinstance(entry, dict):
            raise AppManifestError("page entries must be mappings")
        slug = entry.get("slug")
        view = entry.get("view")
        title = entry.get("title", slug)
        if not isinstance(slug, str) or not isinstance(view, str) or not isinstance(title, str):
            raise AppManifestError("page requires string slug, title, and view")
        _validate_identifier(slug, "page slug")
        _validate_identifier(view, "view")
        if slug in seen_pages:
            raise AppManifestError(f"duplicate page slug: {slug}")
        seen_pages.add(slug)
        pages.append(AppPage(slug=slug, title=title, view=view))

    reads_raw = raw.get("reads") or {}
    writes_raw = raw.get("writes") or {}
    if not isinstance(reads_raw, dict) or not isinstance(writes_raw, dict):
        raise AppManifestError("reads and writes must be mappings")
    return AppManifest(
        name=name,
        title=str(raw.get("title") or name.replace("_", " ").title()),
        description=str(raw.get("description") or ""),
        pages=tuple(pages),
        reads=AppReads(
            tables=_strings(reads_raw.get("tables"), field_name="reads.tables"),
            models=_strings(reads_raw.get("models"), field_name="reads.models"),
        ),
        writes=AppWrites(
            tables=_strings(writes_raw.get("tables"), field_name="writes.tables"),
            actions=_strings(writes_raw.get("actions"), field_name="writes.actions"),
        ),
    )


def _bundled_apps_root() -> Path | None:
    try:
        pkg = resources.files("personal_db.templates.apps")
    except ModuleNotFoundError:
        return None
    with resources.as_file(pkg) as path:
        return path


def list_bundled_apps() -> list[str]:
    root = _bundled_apps_root()
    if root is None or not root.exists():
        return []
    return sorted(
        entry.name for entry in root.iterdir() if entry.is_dir() and (entry / "app.yaml").is_file()
    )


def install_app_template(cfg: Config, name: str) -> Path:
    dest = cfg.apps_dir / name
    if dest.exists():
        raise FileExistsError(f"already installed: {dest}")
    bundled = _bundled_apps_root()
    src = bundled / name if bundled else None
    if src is None or not src.is_dir():
        raise ValueError(f"unknown built-in app: {name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    return dest


def update_app_template(cfg: Config, name: str) -> Path:
    bundled = _bundled_apps_root()
    src = bundled / name if bundled else None
    if src is None or not src.is_dir():
        raise ValueError(f"unknown built-in app: {name}")
    dest = cfg.apps_dir / name
    dest.mkdir(parents=True, exist_ok=True)
    for fname in _APP_TEMPLATE_FILES:
        src_f = src / fname
        if src_f.is_file():
            (dest / fname).write_bytes(src_f.read_bytes())
    return dest


def apply_app_schema(cfg: Config, app_dir: Path) -> None:
    schema_path = app_dir / "schema.sql"
    if not schema_path.is_file():
        return
    con = sqlite3.connect(cfg.db_path)
    try:
        con.executescript(schema_path.read_text())
        con.commit()
    finally:
        con.close()


def discover_apps(cfg: Config) -> dict[str, AppDefinition]:
    """Discover installed apps, falling back to bundled app templates.

    Installed apps in <root>/apps/<name> override bundled apps with the same
    name, which lets users customize an app without changing package files.
    """
    out: dict[str, AppDefinition] = {}
    roots: list[tuple[str, Path]] = []
    bundled = _bundled_apps_root()
    if bundled and bundled.exists():
        roots.append(("bundled", bundled))
    if cfg.apps_dir.exists():
        roots.append(("installed", cfg.apps_dir))

    for source, root in roots:
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "app.yaml"
            if not manifest_path.is_file():
                continue
            try:
                manifest = load_app_manifest(manifest_path)
            except AppManifestError:
                continue
            out[manifest.name] = AppDefinition(
                name=manifest.name,
                root=entry,
                manifest=manifest,
                source=source,
            )
    return dict(sorted(out.items()))


def load_named_queries(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    queries: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in path.read_text().splitlines():
        marker = _QUERY_MARKER_RE.match(line)
        if marker:
            if current_name:
                queries[current_name] = "\n".join(current_lines).strip()
            current_name = marker.group(1)
            current_lines = []
            continue
        if current_name:
            current_lines.append(line)
    if current_name:
        queries[current_name] = "\n".join(current_lines).strip()
    for name, sql in queries.items():
        if not sql:
            raise AppQueryError(f"empty query: {name}")
        first = sql.lstrip().split(None, 1)[0].lower()
        if first not in {"select", "with"}:
            raise AppQueryError(f"named query must be read-only: {name}")
    return queries


def run_named_query(
    cfg: Config, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    con = connect(cfg.db_path, read_only=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params or {}).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def load_app_module(app_dir: Path, app_name: str, stem: str) -> Any:
    path = app_dir / f"{stem}.py"
    if not path.is_file():
        raise AppManifestError(f"app {app_name} has no {stem}.py")
    modname = f"personal_db_app_{app_name}_{stem}"
    sys.modules.pop(modname, None)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise AppManifestError(f"failed to load app module: {app_name}/{stem}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def load_app_view(definition: AppDefinition, page: AppPage) -> Callable[[AppContext], str]:
    module = load_app_module(definition.root, definition.name, "views")
    view = getattr(module, page.view, None)
    if view is None or not callable(view):
        raise AppManifestError(f"view {page.view!r} not found for app {definition.name}")
    return view
