"""Copy bundled tracker templates into the user's data root."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from personal_db.config import Config


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
