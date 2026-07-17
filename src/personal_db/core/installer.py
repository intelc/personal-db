"""Copy bundled tracker templates into the user's data root."""

from __future__ import annotations

import hashlib
import shutil
from importlib import resources
from pathlib import Path

from personal_db.core.config import Config
from personal_db.core.manifest import check_platform_supported, load_manifest
from personal_db.core.validation import compute_files_hash, record_validation

_TRACKER_FILES = (
    "manifest.yaml",
    "ingest.py",
    "schema.sql",
    "visualizations.py",
    "actions.py",     # optional — user-initiated handlers (daemon endpoint added in upcoming work)
    "parsers.py",     # optional — tracker-specific helper module
    "intervals.py",   # optional — tracker-specific helper module
    "tools.py",       # optional — declared mcp_tools entrypoints
)


def _adapter_modules(manifest_path: Path) -> list[str]:
    """Return the list of module names referenced by `OAuthStep.adapter` in
    a manifest. Returns [] if the manifest doesn't exist, doesn't load, or
    has no OAuth steps with adapters. Best-effort: silently ignores parse
    errors so installer code paths can keep going.
    """
    if not manifest_path.is_file():
        return []
    try:
        from personal_db.core.manifest import OAuthStep, load_manifest
        m = load_manifest(manifest_path)
    except Exception:
        return []
    out: list[str] = []
    for step in m.setup_steps:
        if isinstance(step, OAuthStep) and step.adapter:
            module_name = step.adapter.partition(":")[0]
            if module_name:
                out.append(module_name)
    return out


def _hash_dir(path: Path) -> str:
    """Stable hash over the canonical tracker files plus any OAuth adapter
    modules declared in the manifest. Missing files contribute an empty hash."""
    h = hashlib.sha256()
    for name in _TRACKER_FILES:
        f = path / name
        h.update(name.encode())
        h.update(b":")
        if f.is_file():
            h.update(f.read_bytes())
        h.update(b"\n")
    # Also hash any adapter modules declared in the manifest. Sorted for
    # stability (manifest ordering shouldn't affect the hash).
    for module_name in sorted(_adapter_modules(path / "manifest.yaml")):
        f = path / f"{module_name}.py"
        h.update(f"adapter:{module_name}.py".encode())
        h.update(b":")
        if f.is_file():
            h.update(f.read_bytes())
        h.update(b"\n")
    # Migration files are canonical too (drift there should mark the tracker
    # outdated exactly like drift in schema.sql itself).
    migrations_dir = path / "migrations"
    if migrations_dir.is_dir():
        for f in sorted(migrations_dir.iterdir()):
            if not f.is_file():
                continue
            h.update(f"migrations/{f.name}".encode())
            h.update(b":")
            h.update(f.read_bytes())
            h.update(b"\n")
    return h.hexdigest()


def is_outdated(cfg: Config, name: str) -> bool:
    """True if the installed tracker's files differ from the bundled template."""
    installed_dir = cfg.trackers_dir / name
    if not installed_dir.exists():
        return False  # not installed at all → caller handles via list_bundled
    src_pkg = resources.files("personal_db.templates.trackers").joinpath(name)
    if not src_pkg.is_dir():
        return False  # custom tracker (no bundled template) → never marked outdated
    with resources.as_file(src_pkg) as src_path:
        return _hash_dir(installed_dir) != _hash_dir(src_path)


def _copy_migrations_dir(src_path: Path, dest: Path) -> None:
    """Mirror any `migrations/*.sql` files from the bundle into the installed
    tracker dir. Additive (never deletes a file the bundle no longer ships);
    that matches every other canonical file's overwrite-only semantics here."""
    src_migrations = src_path / "migrations"
    if not src_migrations.is_dir():
        return
    dest_migrations = dest / "migrations"
    dest_migrations.mkdir(parents=True, exist_ok=True)
    for f in src_migrations.iterdir():
        if f.is_file():
            (dest_migrations / f.name).write_bytes(f.read_bytes())


def _check_bundled_platform(src_path: Path) -> None:
    manifest_path = src_path / "manifest.yaml"
    if manifest_path.is_file():
        check_platform_supported(load_manifest(manifest_path))


def update_template(cfg: Config, name: str) -> Path:
    """Overwrite canonical tracker files in <root>/trackers/<name>/ from the bundle.
    Also copies any OAuth adapter modules declared in the manifest and any
    migrations/*.sql files. Preserves any other files in the dir. Raises
    ValueError if no bundled template; PlatformUnsupportedError if the
    manifest declares a `platform` list that excludes the current OS.

    Auto-stamps the tracker as validated (core/validation.py) — bundled
    templates are pre-trusted, so `sync_one`'s validation gate shouldn't
    make a user re-run `tracker validate` after every `tracker reinstall`."""
    src_pkg = resources.files("personal_db.templates.trackers").joinpath(name)
    if not src_pkg.is_dir():
        raise ValueError(f"unknown built-in tracker: {name}")
    dest = cfg.trackers_dir / name
    dest.mkdir(parents=True, exist_ok=True)
    with resources.as_file(src_pkg) as src_path:
        _check_bundled_platform(src_path)
        for fname in _TRACKER_FILES:
            src_f = src_path / fname
            if src_f.is_file():
                (dest / fname).write_bytes(src_f.read_bytes())
        # Copy any adapter modules declared in the manifest (Withings has one).
        for module_name in _adapter_modules(src_path / "manifest.yaml"):
            src_f = src_path / f"{module_name}.py"
            if src_f.is_file():
                (dest / f"{module_name}.py").write_bytes(src_f.read_bytes())
        _copy_migrations_dir(src_path, dest)
    record_validation(cfg, name, compute_files_hash(dest))
    return dest


def list_bundled() -> list[str]:
    """Names of all bundled tracker templates."""
    pkg = resources.files("personal_db.templates.trackers")
    out: list[str] = []
    for entry in pkg.iterdir():
        # Skip __init__.py, __pycache__, etc. Only directories with a manifest.yaml count.
        if not entry.is_dir():
            continue
        if not entry.joinpath("manifest.yaml").is_file():
            continue
        out.append(entry.name)
    return sorted(out)


def install_template(cfg: Config, name: str) -> Path:
    """Copy a bundled template into <root>/trackers/<name>. Returns the dest path.

    Raises:
        FileExistsError: if <root>/trackers/<name> already exists.
        ValueError: if no bundled template named `name` exists.
        PlatformUnsupportedError: if the manifest declares a `platform` list
            that excludes the current OS.

    Auto-stamps the tracker as validated (core/validation.py) — see
    update_template's docstring for why bundled templates are pre-trusted.
    """
    dest = cfg.trackers_dir / name
    if dest.exists():
        raise FileExistsError(f"already installed: {dest}")
    src_pkg = resources.files("personal_db.templates.trackers").joinpath(name)
    if not src_pkg.is_dir():
        raise ValueError(f"unknown built-in tracker: {name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(src_pkg) as src_path:
        _check_bundled_platform(src_path)
        shutil.copytree(src_path, dest)
    record_validation(cfg, name, compute_files_hash(dest))
    return dest
