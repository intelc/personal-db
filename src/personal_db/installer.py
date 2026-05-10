"""Copy bundled tracker templates into the user's data root."""

from __future__ import annotations

import hashlib
import shutil
from importlib import resources
from pathlib import Path

from personal_db.config import Config

_TRACKER_FILES = (
    "manifest.yaml",
    "ingest.py",
    "schema.sql",
    "visualizations.py",
    "actions.py",     # optional — user-initiated handlers (daemon endpoint added in upcoming work)
    "parsers.py",     # optional — tracker-specific helper module
    "intervals.py",   # optional — tracker-specific helper module
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
        from personal_db.manifest import OAuthStep, load_manifest
        m = load_manifest(manifest_path)
    except Exception:  # noqa: BLE001 — installer is best-effort here
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


def update_template(cfg: Config, name: str) -> Path:
    """Overwrite canonical tracker files in <root>/trackers/<name>/ from the bundle.
    Also copies any OAuth adapter modules declared in the manifest. Preserves
    any other files in the dir. Raises ValueError if no bundled template."""
    src_pkg = resources.files("personal_db.templates.trackers").joinpath(name)
    if not src_pkg.is_dir():
        raise ValueError(f"unknown built-in tracker: {name}")
    dest = cfg.trackers_dir / name
    dest.mkdir(parents=True, exist_ok=True)
    with resources.as_file(src_pkg) as src_path:
        for fname in _TRACKER_FILES:
            src_f = src_path / fname
            if src_f.is_file():
                (dest / fname).write_bytes(src_f.read_bytes())
        # Copy any adapter modules declared in the manifest (Withings has one).
        for module_name in _adapter_modules(src_path / "manifest.yaml"):
            src_f = src_path / f"{module_name}.py"
            if src_f.is_file():
                (dest / f"{module_name}.py").write_bytes(src_f.read_bytes())
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
    """
    dest = cfg.trackers_dir / name
    if dest.exists():
        raise FileExistsError(f"already installed: {dest}")
    src_pkg = resources.files("personal_db.templates.trackers").joinpath(name)
    if not src_pkg.is_dir():
        raise ValueError(f"unknown built-in tracker: {name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(src_pkg) as src_path:
        shutil.copytree(src_path, dest)
    return dest
