"""Copy bundled tracker templates into the user's data root."""

from __future__ import annotations

import hashlib
import shutil
from importlib import resources
from pathlib import Path

from personal_db.config import Config

_TRACKER_FILES = ("manifest.yaml", "ingest.py", "schema.sql", "visualizations.py")


def _hash_dir(path: Path) -> str:
    """Stable hash over the canonical tracker files. Missing files contribute
    an empty hash (so removing a file from the bundle is treated as drift)."""
    h = hashlib.sha256()
    for name in _TRACKER_FILES:
        f = path / name
        h.update(name.encode())
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
    """Overwrite the 3 canonical files in <root>/trackers/<name>/ from the bundle.
    Preserves any other files in the dir. Raises ValueError if no bundled template."""
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
